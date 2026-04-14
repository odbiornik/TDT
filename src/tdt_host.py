from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import serial
from serial import SerialException
from serial.tools import list_ports

import tdt_questplus as tdtq
import tdt_report as tdtr


def runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = runtime_root()
PROTOCOL_VERSION = "tdt-1"


@dataclass
class SerialConfig:
    baudrate: int = 115200
    settle_time_s: float = 3.0
    hello_timeout_s: float = 5.0
    read_timeout_s: float = 0.25
    write_timeout_s: float = 1.0
    session_prepare_timeout_s: float = 12.0
    state_timeout_s: float = 3.0
    retry_settle_time_s: float = 4.5
    retry_hello_timeout_s: float = 7.0
    port_probe_attempts: int = 2


def format_duration_compact(total_seconds: float) -> str:
    total_seconds_int = max(0, int(round(total_seconds)))
    hours, remainder = divmod(total_seconds_int, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


class SessionLogger:
    def __init__(self, session_dir: Path) -> None:
        self.session_dir = session_dir
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.session_dir / "events.jsonl"

    def log_event(self, event_type: str, payload: dict[str, Any]) -> None:
        event = dict(
            timestamp=datetime.now().isoformat(timespec="milliseconds"),
            type=event_type,
            payload=tdtq.to_builtin(payload),
        )
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=True) + "\n")

    def write_summary(self, summary: dict[str, Any]) -> None:
        path = self.session_dir / "summary.json"
        with path.open("w", encoding="utf-8") as handle:
            json.dump(tdtq.to_builtin(summary), handle, indent=2, ensure_ascii=True)

    def write_trials(self, trials: list[dict[str, Any]]) -> None:
        if not trials:
            return

        preferred_order = [
            "session_trial_number",
            "phase",
            "sanity_kind",
            "sanity_label",
            "attempt_number",
            "adaptive_completed_trials",
            "requested_soa_ms",
            "lead_led",
            "flash_duration_ms",
            "flash_rgb_level",
            "expected_response",
            "response",
            "sanity_correct",
            "button",
            "rt_ms",
            "timed_out",
            "invalid",
            "updated_staircase",
            "threshold50_ms",
            "threshold75_ms",
            "jnd_ms",
            "guess_rate",
            "lapse_rate",
            "ci50_width_ms",
            "ci50_low_ms",
            "ci50_high_ms",
            "ci75_width_ms",
            "ci75_low_ms",
            "ci75_high_ms",
            "entropy_normalized",
            "early_stop_active",
        ]

        fieldnames: list[str] = []
        for record in trials:
            for key in record:
                if key not in fieldnames:
                    fieldnames.append(key)

        ordered_fieldnames = [field for field in preferred_order if field in fieldnames]
        ordered_fieldnames.extend(
            sorted(field for field in fieldnames if field not in ordered_fieldnames)
        )

        path = self.session_dir / "trials.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=ordered_fieldnames)
            writer.writeheader()
            for record in trials:
                writer.writerow(tdtq.to_builtin(record))


class AtomConnection:
    def __init__(self, config: SerialConfig) -> None:
        self.config = config
        self.serial_port: serial.Serial | None = None
        self.connected_port: str | None = None
        self.hello_ack: dict[str, Any] | None = None
        self.clear_on_close = True

    def connect(self, port: str | None = None) -> dict[str, Any]:
        if port is None:
            port, hello_ack = self._discover_port()
        else:
            hello_ack = self._try_port_with_retries(port)
            if hello_ack is None:
                raise RuntimeError(f"Could not handshake with device on {port}.")

        self.serial_port = self._open_serial(port)
        self.connected_port = port
        self.hello_ack = self._handshake(self.serial_port)
        return self.hello_ack

    def close(self) -> None:
        if self.serial_port is not None:
            try:
                if self.clear_on_close:
                    self.send_request(
                        {"type": "set_idle"},
                        expected_types={"idle_ack"},
                        timeout_s=self.config.state_timeout_s,
                    )
            except Exception:
                pass
            self.serial_port.close()
            self.serial_port = None

    def send_message(self, payload: dict[str, Any]) -> None:
        if self.serial_port is None:
            raise RuntimeError("Serial connection is not open.")
        line = json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n"
        self.serial_port.write(line.encode("utf-8"))
        self.serial_port.flush()

    def read_message(self, timeout_s: float) -> dict[str, Any] | None:
        if self.serial_port is None:
            raise RuntimeError("Serial connection is not open.")
        return self._read_message(self.serial_port, timeout_s)

    def run_trial(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.send_message(payload)

        total_timeout_s = (
            payload.get("prestim_delay_ms", 0)
            + payload.get("soa_ms", 0)
            + (2 * payload.get("flash_ms", 0))
            + payload.get("response_timeout_ms", 0)
            + 2500
        ) / 1000.0
        deadline = time.monotonic() + max(total_timeout_s, 4.0)

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            message = self.read_message(max(remaining, 0.05))
            if message is None:
                continue

            message_type = message.get("type")
            if message_type == "trial_result" and message.get("trial_id") == payload.get("trial_id"):
                return message
            if message_type == "error":
                raise RuntimeError(
                    f"Device reported an error during trial {payload.get('trial_id')}: "
                    f"{message.get('message', 'unknown error')}"
                )

        raise TimeoutError(f"Timed out while waiting for trial {payload.get('trial_id')} result.")

    def send_request(
        self,
        payload: dict[str, Any],
        *,
        expected_types: set[str],
        timeout_s: float,
    ) -> dict[str, Any]:
        self.send_message(payload)
        deadline = time.monotonic() + timeout_s

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            message = self.read_message(max(remaining, 0.05))
            if message is None:
                continue

            message_type = message.get("type")
            if message_type in expected_types:
                return message
            if message_type == "error":
                raise RuntimeError(message.get("message", "Device reported an error."))

        raise TimeoutError(
            f"Timed out while waiting for {', '.join(sorted(expected_types))}."
        )

    def prepare_session(self) -> dict[str, Any]:
        return self.send_request(
            {"type": "prepare_session"},
            expected_types={"prepare_session_ack"},
            timeout_s=self.config.session_prepare_timeout_s,
        )

    def complete_session(self) -> dict[str, Any]:
        response = self.send_request(
            {"type": "complete_session"},
            expected_types={"complete_session_ack"},
            timeout_s=self.config.state_timeout_s,
        )
        self.clear_on_close = False
        return response

    def _discover_port(self) -> tuple[str, dict[str, Any]]:
        errors: list[str] = []
        available_ports = sorted(list_ports.comports(), key=self._port_sort_key)
        candidate_ports = [
            port_info for port_info in available_ports if not self._is_skippable_port(port_info)
        ]
        preferred_ports = [
            port_info for port_info in candidate_ports if self._is_likely_atom_port(port_info)
        ]
        preferred_devices = {port_info.device for port_info in preferred_ports}
        fallback_ports = [
            port_info for port_info in candidate_ports if port_info.device not in preferred_devices
        ]

        for port_group in (preferred_ports, fallback_ports):
            for port_info in port_group:
                try:
                    hello_ack = self._try_port_with_retries(port_info.device)
                except Exception as exc:
                    errors.append(f"{port_info.device}: {exc}")
                    continue

                if hello_ack is not None:
                    return port_info.device, hello_ack

        if candidate_ports:
            details = "\n".join(errors) if errors else "No compatible serial ports responded."
        elif available_ports:
            details = "Only skipped serial ports were found (for example Bluetooth serial links)."
        else:
            details = "No serial ports were available."
        raise RuntimeError(
            "Could not automatically find an Atom device speaking the expected protocol.\n"
            + details
        )

    def _try_port_with_retries(self, port: str) -> dict[str, Any] | None:
        last_exception: Exception | None = None
        attempts = max(1, self.config.port_probe_attempts)
        for attempt_index in range(attempts):
            settle_time_s = (
                self.config.settle_time_s
                if attempt_index == 0
                else self.config.retry_settle_time_s
            )
            hello_timeout_s = (
                self.config.hello_timeout_s
                if attempt_index == 0
                else self.config.retry_hello_timeout_s
            )
            try:
                return self._try_port(
                    port,
                    settle_time_s=settle_time_s,
                    hello_timeout_s=hello_timeout_s,
                )
            except Exception as exc:
                last_exception = exc
        if last_exception is not None:
            raise last_exception
        return None

    def _try_port(
        self,
        port: str,
        *,
        settle_time_s: float | None = None,
        hello_timeout_s: float | None = None,
    ) -> dict[str, Any] | None:
        with self._open_serial(port, settle_time_s=settle_time_s) as serial_port:
            return self._handshake(serial_port, timeout_s=hello_timeout_s)

    def _open_serial(self, port: str, *, settle_time_s: float | None = None) -> serial.Serial:
        serial_port = serial.Serial()
        serial_port.port = port
        serial_port.baudrate = self.config.baudrate
        serial_port.timeout = self.config.read_timeout_s
        serial_port.write_timeout = self.config.write_timeout_s
        serial_port.dsrdtr = False
        serial_port.rtscts = False
        serial_port.xonxoff = False
        serial_port.dtr = False
        serial_port.rts = False
        serial_port.open()
        time.sleep(self.config.settle_time_s if settle_time_s is None else settle_time_s)
        serial_port.dtr = False
        serial_port.rts = False
        serial_port.reset_input_buffer()
        serial_port.reset_output_buffer()
        return serial_port

    def _handshake(
        self,
        serial_port: serial.Serial,
        *,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        hello_payload = {"type": "hello"}
        line = json.dumps(hello_payload, ensure_ascii=True, separators=(",", ":")) + "\n"
        serial_port.write(line.encode("utf-8"))
        serial_port.flush()

        deadline = time.monotonic() + (
            self.config.hello_timeout_s if timeout_s is None else timeout_s
        )
        while time.monotonic() < deadline:
            message = self._read_message(serial_port, deadline - time.monotonic())
            if message is None:
                continue
            if (
                message.get("type") == "hello_ack"
                and message.get("protocol") == PROTOCOL_VERSION
            ):
                return message
        raise RuntimeError("Handshake timed out.")

    def _read_message(
        self,
        serial_port: serial.Serial,
        timeout_s: float,
    ) -> dict[str, Any] | None:
        previous_timeout = serial_port.timeout
        serial_port.timeout = max(timeout_s, 0.05)
        try:
            raw = serial_port.readline()
        finally:
            serial_port.timeout = previous_timeout

        if not raw:
            return None

        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            return None

        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _port_haystack(port_info: list_ports.ListPortInfo) -> str:
        return " ".join(
            filter(
                None,
                [
                    port_info.device,
                    port_info.description,
                    port_info.manufacturer,
                    port_info.product,
                    port_info.hwid,
                ],
            )
        ).lower()

    @classmethod
    def _is_skippable_port(cls, port_info: list_ports.ListPortInfo) -> bool:
        haystack = cls._port_haystack(port_info)
        return (
            "bluetooth" in haystack
            or "bthenum" in haystack
            or "rfcomm" in haystack
        )

    @classmethod
    def _is_likely_atom_port(cls, port_info: list_ports.ListPortInfo) -> bool:
        if cls._is_skippable_port(port_info):
            return False
        haystack = cls._port_haystack(port_info)
        return (
            port_info.vid is not None
            or "m5" in haystack
            or "atom" in haystack
            or "esp32" in haystack
            or "usb" in haystack
            or "cdc" in haystack
            or "jtag" in haystack
        )

    @classmethod
    def _port_sort_key(cls, port_info: list_ports.ListPortInfo) -> tuple[int, str]:
        haystack = cls._port_haystack(port_info)
        score = 0
        if cls._is_skippable_port(port_info):
            score -= 100
        if port_info.vid is not None:
            score += 8
        if "m5" in haystack:
            score += 20
        if "atom" in haystack:
            score += 10
        if "esp32" in haystack:
            score += 10
        if "usb" in haystack or "cdc" in haystack:
            score += 4
        if "jtag" in haystack or "serial" in haystack:
            score += 2
        return (-score, port_info.device)


def serial_port_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for port_info in sorted(list_ports.comports(), key=AtomConnection._port_sort_key):
        rows.append(
            dict(
                device=port_info.device,
                description=port_info.description or "",
                manufacturer=port_info.manufacturer or "",
                product=port_info.product or "",
                hwid=port_info.hwid or "",
                vid=(
                    None
                    if port_info.vid is None
                    else f"0x{int(port_info.vid):04X}"
                ),
                pid=(
                    None
                    if port_info.pid is None
                    else f"0x{int(port_info.pid):04X}"
                ),
                likely_atom=AtomConnection._is_likely_atom_port(port_info),
                skipped=AtomConnection._is_skippable_port(port_info),
            )
        )
    return rows


def build_session_dir(base_dir: Path, subject_id: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_subject = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in subject_id
    )
    return base_dir / f"{timestamp}_{safe_subject}"


def default_config() -> tdtq.ExperimentConfig:
    return tdtq.ExperimentConfig()


def choose_lead_led(soa_ms: int, rng: random.Random) -> str:
    if soa_ms <= 0:
        return "simultaneous"
    return rng.choice(["flash1", "flash2"])


def attach_summary_fields(record: dict[str, Any], summary: dict[str, Any]) -> None:
    record["threshold50_ms"] = summary["estimates"]["threshold50_ms"]
    record["threshold75_ms"] = summary["estimates"]["threshold75_ms"]
    record["jnd_ms"] = summary["estimates"]["jnd_ms"]
    record["guess_rate"] = summary["estimates"]["guess_rate"]
    record["lapse_rate"] = summary["estimates"]["lapse_rate"]
    record["ci50_width_ms"] = summary["reliability"]["threshold50_ci95_width_ms"]
    record["ci50_low_ms"] = summary["reliability"]["threshold50_ci95_low_ms"]
    record["ci50_high_ms"] = summary["reliability"]["threshold50_ci95_high_ms"]
    record["ci75_width_ms"] = summary["reliability"]["threshold75_ci95_width_ms"]
    record["ci75_low_ms"] = summary["reliability"]["threshold75_ci95_low_ms"]
    record["ci75_high_ms"] = summary["reliability"]["threshold75_ci95_high_ms"]
    record["entropy_normalized"] = summary["reliability"]["mean_entropy_normalized"]
    record["early_stop_active"] = summary["early_stop"]["active"]


def run_sanity_trial(
    connection: AtomConnection,
    config: tdtq.ExperimentConfig,
    logger: SessionLogger,
    rng: random.Random,
    *,
    sanity_trial_number: int,
    adaptive_completed_trials: int,
    summary: dict[str, Any],
) -> dict[str, Any]:
    sanity_spec = tdtq.next_sanity_trial_spec(
        config,
        summary,
        sanity_trials_completed=sanity_trial_number - 1,
    )
    soa_ms = int(sanity_spec["soa_ms"])
    lead_led = choose_lead_led(soa_ms, rng)
    payload = tdtq.build_trial_command(
        sanity_trial_number,
        soa_ms,
        config,
        phase="sanity",
        lead_led=lead_led,
    )
    result = connection.run_trial(payload)

    response = str(result.get("response", "")).lower()
    expected_response = str(sanity_spec["expected_response"]).lower()
    sanity_correct = response == expected_response if response in {"yes", "no"} else None

    record = dict(
        phase="sanity",
        attempt_number=sanity_trial_number,
        adaptive_completed_trials=adaptive_completed_trials,
        sanity_kind=sanity_spec["kind"],
        sanity_label=sanity_spec["label"],
        expected_response=expected_response,
        sanity_correct=sanity_correct,
        requested_soa_ms=soa_ms,
        lead_led=result.get("lead_led", lead_led),
        flash_duration_ms=config.flash_duration_ms,
        flash_rgb_level=result.get("flash_rgb_level", config.flash_rgb_level),
        response=result.get("response"),
        button=result.get("button"),
        rt_ms=result.get("rt_ms"),
        timed_out=result.get("timed_out"),
        invalid=result.get("invalid"),
        updated_staircase=False,
    )
    attach_summary_fields(record, summary)
    logger.log_event("sanity_trial", record)
    return record


def run_practice(
    connection: AtomConnection,
    config: tdtq.ExperimentConfig,
    logger: SessionLogger,
    rng: random.Random,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for trial_id, soa_ms in enumerate(tdtq.practice_schedule(config), start=1):
        lead_led = choose_lead_led(soa_ms, rng)
        payload = tdtq.build_trial_command(
            trial_id,
            soa_ms,
            config,
            phase="practice",
            lead_led=lead_led,
        )
        result = connection.run_trial(payload)
        record = dict(
            phase="practice",
            attempt_number=trial_id,
            requested_soa_ms=soa_ms,
            lead_led=result.get("lead_led", lead_led),
            flash_duration_ms=config.flash_duration_ms,
            flash_rgb_level=result.get("flash_rgb_level", config.flash_rgb_level),
            response=result.get("response"),
            button=result.get("button"),
            rt_ms=result.get("rt_ms"),
            timed_out=result.get("timed_out"),
            invalid=result.get("invalid"),
        )
        records.append(record)
        logger.log_event("practice_trial", record)
        time.sleep(config.inter_trial_interval_ms / 1000.0)
    return records


def run_main_session(
    connection: AtomConnection,
    config: tdtq.ExperimentConfig,
    logger: SessionLogger,
    rng: random.Random,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    staircase = tdtq.build_staircase(config)
    trials: list[dict[str, Any]] = []
    summary = tdtq.summarize_staircase(
        staircase,
        config,
        completed_trials=0,
        sanity_summary=tdtq.summarize_sanity_trials([], config),
    )

    completed_trials = 0
    adaptive_attempt_number = 0
    sanity_trial_number = 0
    last_sanity_trigger_completed_trials: int | None = None
    max_attempts = config.trial_count + max(20, config.trial_count // 2)

    while completed_trials < config.trial_count and adaptive_attempt_number < max_attempts:
        adaptive_attempt_number += 1
        next_stim = staircase.next_stim
        soa_ms = int(round(float(next_stim["intensity"])))
        lead_led = choose_lead_led(soa_ms, rng)
        payload = tdtq.build_trial_command(
            adaptive_attempt_number,
            soa_ms,
            config,
            phase="main",
            lead_led=lead_led,
        )
        result = connection.run_trial(payload)

        record = dict(
            phase="main",
            attempt_number=adaptive_attempt_number,
            adaptive_completed_trials=completed_trials,
            requested_soa_ms=soa_ms,
            lead_led=result.get("lead_led", lead_led),
            flash_duration_ms=config.flash_duration_ms,
            flash_rgb_level=result.get("flash_rgb_level", config.flash_rgb_level),
            response=result.get("response"),
            button=result.get("button"),
            rt_ms=result.get("rt_ms"),
            timed_out=result.get("timed_out"),
            invalid=result.get("invalid"),
            updated_staircase=False,
        )

        outcome = tdtq.outcome_from_trial_result(result)
        if outcome is not None:
            staircase.update(stim=next_stim, outcome=outcome)
            completed_trials += 1
            current_sanity_summary = tdtq.summarize_sanity_trials(
                [trial for trial in trials if trial.get("phase") == "sanity"],
                config,
            )
            summary = tdtq.summarize_staircase(
                staircase,
                config,
                completed_trials,
                sanity_summary=current_sanity_summary,
            )
            summary["sanity_checks"] = current_sanity_summary
            record["updated_staircase"] = True
            record["adaptive_completed_trials"] = completed_trials
            attach_summary_fields(record, summary)
            logger.log_event("adaptive_summary", summary)

        trials.append(record)
        logger.log_event("main_trial", record)

        if summary["early_stop"]["active"]:
            break

        if tdtq.should_run_sanity_trial(
            config,
            completed_trials=completed_trials,
            last_trigger_completed_trials=last_sanity_trigger_completed_trials,
        ):
            time.sleep(config.inter_trial_interval_ms / 1000.0)
            sanity_trial_number += 1
            sanity_record = run_sanity_trial(
                connection,
                config,
                logger,
                rng,
                sanity_trial_number=sanity_trial_number,
                adaptive_completed_trials=completed_trials,
                summary=summary,
            )
            trials.append(sanity_record)
            last_sanity_trigger_completed_trials = completed_trials

        time.sleep(config.inter_trial_interval_ms / 1000.0)

    summary["sanity_checks"] = tdtq.summarize_sanity_trials(
        [trial for trial in trials if trial.get("phase") == "sanity"],
        config,
    )
    final_summary = dict(
        config=config.to_dict(),
        summary=summary,
        attempts_total=adaptive_attempt_number,
        adaptive_trials_completed=completed_trials,
        sanity_trials_completed=sanity_trial_number,
    )
    return trials, final_summary


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a TDT/SJ QUEST+ session against an AtomS3 Lite."
    )
    parser.add_argument("--subject", default="test_subject", help="Session subject identifier.")
    parser.add_argument("--port", default=None, help="Optional serial port override, e.g. COM7.")
    parser.add_argument(
        "--list-ports",
        action="store_true",
        help="List detected serial ports and exit.",
    )
    parser.add_argument(
        "--flash-level",
        type=int,
        default=None,
        help="Optional LED stimulus brightness (0-255) sent to the Atom for this session.",
    )
    parser.add_argument(
        "--skip-practice",
        action="store_true",
        help="Skip the practice block and go directly into the adaptive session.",
    )
    parser.add_argument(
        "--skip-sanity",
        action="store_true",
        help="Disable inserted sanity control trials for this session.",
    )
    parser.add_argument("--trial-count", type=int, default=None, help="Override adaptive trial count.")
    parser.add_argument(
        "--min-trials-for-early-stop",
        type=int,
        default=None,
        help="Override minimum completed adaptive trials before early-stop can activate.",
    )
    parser.add_argument(
        "--ci95-width-ms",
        type=float,
        default=None,
        help="Override threshold 50%% CI95 width target in milliseconds.",
    )
    parser.add_argument(
        "--entropy-threshold",
        type=float,
        default=None,
        help="Override normalized entropy threshold for early-stop (e.g. 0.45).",
    )
    parser.add_argument(
        "--sanity-min-rate",
        type=float,
        default=None,
        help="Override minimum sanity-correct rate for early-stop (0.0-1.0).",
    )
    parser.add_argument(
        "--practice-trial-count",
        type=int,
        default=None,
        help="Override number of practice trials.",
    )
    parser.add_argument("--min-soa-ms", type=int, default=None, help="Override minimum SOA.")
    parser.add_argument("--max-soa-ms", type=int, default=None, help="Override maximum SOA.")
    parser.add_argument("--step-ms", type=int, default=None, help="Override SOA grid step.")
    parser.add_argument(
        "--flash-duration-ms",
        type=int,
        default=None,
        help="Override flash duration in ms.",
    )
    parser.add_argument(
        "--response-timeout-ms",
        type=int,
        default=None,
        help="Override response timeout in ms.",
    )
    parser.add_argument(
        "--prestim-delay-ms",
        type=int,
        default=None,
        help="Override pre-stimulus delay in ms.",
    )
    parser.add_argument(
        "--iti-ms",
        type=int,
        default=None,
        help="Override inter-trial interval in ms.",
    )
    return parser.parse_args(argv)


def apply_cli_config_overrides(
    config: tdtq.ExperimentConfig,
    args: argparse.Namespace,
) -> None:
    if args.trial_count is not None:
        config.trial_count = max(1, int(args.trial_count))
    if args.min_trials_for_early_stop is not None:
        config.min_trials_for_early_stop = max(1, int(args.min_trials_for_early_stop))
    if args.ci95_width_ms is not None:
        config.stop_criteria.threshold50_ci95_width_ms = max(0.1, float(args.ci95_width_ms))
    if args.entropy_threshold is not None:
        config.stop_criteria.mean_entropy_normalized = max(0.0, min(1.0, float(args.entropy_threshold)))
    if args.sanity_min_rate is not None:
        config.stop_criteria.minimum_sanity_correct_rate = max(0.0, min(1.0, float(args.sanity_min_rate)))
    if args.practice_trial_count is not None:
        config.practice_trial_count = max(0, int(args.practice_trial_count))
    if args.min_soa_ms is not None:
        config.min_soa_ms = int(args.min_soa_ms)
    if args.max_soa_ms is not None:
        config.max_soa_ms = int(args.max_soa_ms)
    if args.step_ms is not None:
        config.step_ms = max(1, int(args.step_ms))
    if args.flash_duration_ms is not None:
        config.flash_duration_ms = max(1, int(args.flash_duration_ms))
    if args.response_timeout_ms is not None:
        config.response_timeout_ms = max(100, int(args.response_timeout_ms))
    if args.prestim_delay_ms is not None:
        config.prestim_delay_ms = max(0, int(args.prestim_delay_ms))
    if args.iti_ms is not None:
        config.inter_trial_interval_ms = max(0, int(args.iti_ms))
    if config.min_soa_ms > config.max_soa_ms:
        config.min_soa_ms, config.max_soa_ms = config.max_soa_ms, config.min_soa_ms


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.list_ports:
        rows = serial_port_rows()
        if not rows:
            print("No serial ports detected.")
            return 0
        print("Detected serial ports:")
        for row in rows:
            tags: list[str] = []
            if row["likely_atom"]:
                tags.append("likely-atom")
            if row["skipped"]:
                tags.append("skipped")
            tag_display = "" if not tags else f" [{' '.join(tags)}]"
            print(f"  {row['device']}{tag_display}")
            if row["description"]:
                print(f"    description: {row['description']}")
            if row["manufacturer"]:
                print(f"    manufacturer: {row['manufacturer']}")
            if row["product"]:
                print(f"    product: {row['product']}")
            if row["vid"] is not None and row["pid"] is not None:
                print(f"    vid:pid = {row['vid']}:{row['pid']}")
        return 0

    config = default_config()
    apply_cli_config_overrides(config, args)
    if args.flash_level is not None:
        config.flash_rgb_level = max(0, min(255, int(args.flash_level)))
    if args.skip_sanity:
        config.sanity_checks.enabled = False

    sessions_root = PROJECT_ROOT / "data" / "sessions"
    serial_config = SerialConfig()
    lead_led_rng = random.Random()
    session_started_monotonic = time.monotonic()
    practice_duration_s: float | None = None

    session_dir = build_session_dir(sessions_root, args.subject)
    started_at = datetime.now().isoformat(timespec="milliseconds")
    logger = SessionLogger(session_dir)
    logger.log_event(
        "session_start",
        dict(
            subject=args.subject,
            started_at=started_at,
            config=config.to_dict(),
        ),
    )

    connection = AtomConnection(serial_config)
    try:
        hello_ack = connection.connect(args.port)
        logger.log_event("device_connected", hello_ack)

        print(f"Connected to {connection.connected_port}")
        print(json.dumps(hello_ack, indent=2, ensure_ascii=True))

        print("")
        print("Showing session start sequence...")
        prepare_ack = connection.prepare_session()
        logger.log_event("session_prepare_sequence", prepare_ack)

        practice_trials: list[dict[str, Any]] = []
        if not args.skip_practice:
            print("")
            print("Running practice block...")
            practice_started_monotonic = time.monotonic()
            practice_trials = run_practice(connection, config, logger, lead_led_rng)
            practice_duration_s = time.monotonic() - practice_started_monotonic

        print("")
        print("Running adaptive QUEST+ block...")
        adaptive_started_monotonic = time.monotonic()
        main_trials, summary = run_main_session(connection, config, logger, lead_led_rng)
        adaptive_duration_s = time.monotonic() - adaptive_started_monotonic

        complete_ack = connection.complete_session()
        logger.log_event("session_complete_indicator", complete_ack)

        combined_trials = practice_trials + main_trials
        for session_trial_number, record in enumerate(combined_trials, start=1):
            record["session_trial_number"] = session_trial_number

        logger.write_trials(combined_trials)
        completed_at = datetime.now().isoformat(timespec="milliseconds")
        full_duration_s = time.monotonic() - session_started_monotonic
        summary["session_timing"] = dict(
            started_at=started_at,
            completed_at=completed_at,
            total_duration_s=full_duration_s,
            total_duration_display=format_duration_compact(full_duration_s),
            practice_duration_s=practice_duration_s,
            practice_duration_display=(
                None
                if practice_duration_s is None
                else format_duration_compact(practice_duration_s)
            ),
            adaptive_duration_s=adaptive_duration_s,
            adaptive_duration_display=format_duration_compact(adaptive_duration_s),
        )
        logger.write_summary(summary)
        export_info: dict[str, Any] | None = None
        export_error: str | None = None
        try:
            export_info = tdtr.export_session_artifacts(
                session_dir,
                notebook_path=PROJECT_ROOT / "src" / "quest.ipynb",
            )
            logger.log_event("session_exports", export_info)
        except Exception as exc:
            export_error = str(exc)
            logger.log_event("session_exports_error", {"message": export_error})
        logger.log_event(
            "session_complete",
            dict(
                started_at=started_at,
                completed_at=completed_at,
                session_timing=summary["session_timing"],
                summary=summary,
                exports=export_info,
                export_error=export_error,
            ),
        )

        print("")
        print(f"Saved logs to: {session_dir}")
        if export_info:
            if export_info.get("summary_xlsx"):
                print(f"Saved Excel summary to: {export_info['summary_xlsx']}")
            elif export_info.get("summary_xlsx_error"):
                print(f"Excel export skipped: {export_info['summary_xlsx_error']}")
            if export_info.get("session_plot_html"):
                print(f"Saved plot to: {export_info['session_plot_html']}")
            if export_info.get("session_report_html"):
                print(f"Saved report to: {export_info['session_report_html']}")
        elif export_error:
            print(f"Report export warning: {export_error}")
        print("")
        print("Session summary:")
        for line in tdtq.summary_lines(summary["summary"], config):
            print(f"  {line}")
        timing = summary["session_timing"]
        print(f"  Total time: {timing['total_duration_display']} ({timing['total_duration_s']:.1f} s)")
        if timing["practice_duration_s"] is not None:
            print(
                f"  Practice duration: {timing['practice_duration_display']} "
                f"({timing['practice_duration_s']:.1f} s)"
            )
        print(
            f"  Adaptive duration: {timing['adaptive_duration_display']} "
            f"({timing['adaptive_duration_s']:.1f} s)"
        )
        return 0

    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())

