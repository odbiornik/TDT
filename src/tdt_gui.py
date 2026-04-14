from __future__ import annotations

import queue
import subprocess
import sys
import threading
import tkinter as tk
import ctypes
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any
import webbrowser

import tdt_report as tdtr
import tdt_host

def runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = runtime_root()
SESSIONS_DIR = PROJECT_ROOT / "data" / "sessions"


class TdtGuiApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("TDT Studio")
        self.root.geometry("1320x860")

        self.subject_var = tk.StringVar(value="test_subject")
        self.port_var = tk.StringVar(value="")
        self.flash_var = tk.StringVar(value="")
        self.skip_practice_var = tk.BooleanVar(value=False)
        self.skip_sanity_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Ready")
        self.param_vars: dict[str, tk.StringVar] = {}
        self.param_defaults: dict[str, str] = {}

        self._process: subprocess.Popen[str] | None = None
        self._worker_thread: threading.Thread | None = None
        self._output_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._selected_session: Path | None = None
        self._session_paths: list[Path] = []

        self._configure_styles()
        self._build_ui()
        self.refresh_sessions()
        self._poll_output_queue()

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.root.configure(bg="#f0f0f0")
        style.configure("TLabel", padding=2)
        style.configure("TLabelframe", padding=6)
        style.configure("TLabelframe.Label", font=("Segoe UI", 10, "bold"))
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))
        style.configure("Danger.TButton", font=("Segoe UI", 9, "bold"))
        style.configure("Status.TLabel", padding=(8, 5), font=("Segoe UI", 9))

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(0, weight=5)
        outer.columnconfigure(1, weight=7)
        outer.rowconfigure(0, weight=0)
        outer.rowconfigure(1, weight=1)
        outer.rowconfigure(2, weight=2)
        outer.rowconfigure(3, weight=0)

        controls = ttk.LabelFrame(outer, text="Run Session", padding=10)
        controls.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        controls.columnconfigure(6, weight=1)

        ttk.Label(controls, text="Subject:").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.subject_var, width=20).grid(row=0, column=1, padx=(4, 12))

        ttk.Label(controls, text="Port (optional):").grid(row=0, column=2, sticky="w")
        ttk.Entry(controls, textvariable=self.port_var, width=12).grid(row=0, column=3, padx=(4, 12))

        ttk.Label(controls, text="Flash 0-255 (optional):").grid(row=0, column=4, sticky="w")
        ttk.Entry(controls, textvariable=self.flash_var, width=8).grid(row=0, column=5, padx=(4, 12))

        ttk.Checkbutton(controls, text="Skip practice", variable=self.skip_practice_var).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(controls, text="Skip sanity checks", variable=self.skip_sanity_var).grid(row=1, column=2, columnspan=2, sticky="w", pady=(8, 0))

        run_buttons = ttk.Frame(controls)
        run_buttons.grid(row=1, column=4, columnspan=3, sticky="e", pady=(8, 0))
        ttk.Button(run_buttons, text="List Ports", command=self.list_ports).pack(side=tk.LEFT, padx=(0, 6))
        self.start_button = ttk.Button(run_buttons, text="Start Session", style="Accent.TButton", command=self.start_session)
        self.start_button.pack(side=tk.LEFT, padx=(0, 6))
        self.stop_button = ttk.Button(run_buttons, text="Stop", style="Danger.TButton", command=self.stop_session, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT)

        left = ttk.LabelFrame(outer, text="Previous Sessions", padding=8)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        top_left_buttons = ttk.Frame(left)
        top_left_buttons.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(top_left_buttons, text="Refresh", command=self.refresh_sessions).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(top_left_buttons, text="Open Report (HTML)", command=self.open_selected_report).pack(side=tk.LEFT)

        self.sessions_list = tk.Listbox(
            left,
            exportselection=False,
            highlightthickness=1,
            font=("Consolas", 10),
        )
        self.sessions_list.grid(row=1, column=0, sticky="nsew")
        self.sessions_list.bind("<<ListboxSelect>>", self.on_select_session)

        params = ttk.LabelFrame(outer, text="QUEST+ / Session Parameters", padding=8)
        params.grid(row=1, column=1, sticky="nsew")
        params.columnconfigure(0, weight=1)
        params.columnconfigure(1, weight=1)
        params.columnconfigure(2, weight=1)
        params.columnconfigure(3, weight=1)

        # key: (label, cli_flag, default)
        param_specs: list[tuple[str, str, str]] = [
            ("Trial count", "--trial-count", "64"),
            ("Min trials early stop", "--min-trials-for-early-stop", "32"),
            ("CI95 width (ms)", "--ci95-width-ms", "5.0"),
            ("Entropy threshold (0-1, empty=off)", "--entropy-threshold", ""),
            ("Sanity min rate (0-1)", "--sanity-min-rate", "0.5"),
            ("Practice trial count", "--practice-trial-count", "8"),
            ("SOA min (ms)", "--min-soa-ms", "0"),
            ("SOA max (ms)", "--max-soa-ms", "100"),
            ("SOA step (ms)", "--step-ms", "1"),
            ("Flash duration (ms)", "--flash-duration-ms", "10"),
            ("Response timeout (ms)", "--response-timeout-ms", "8000"),
            ("Prestim delay (ms)", "--prestim-delay-ms", "500"),
            ("Inter-trial interval (ms)", "--iti-ms", "1500"),
        ]
        for index, (label, flag, default) in enumerate(param_specs):
            row = index // 4
            col = (index % 4) * 2
            var = tk.StringVar(value=default)
            self.param_vars[flag] = var
            self.param_defaults[flag] = default
            ttk.Label(params, text=label).grid(row=row, column=col, sticky="w", padx=(0, 4), pady=(2, 2))
            ttk.Entry(params, textvariable=var, width=10).grid(row=row, column=col + 1, sticky="ew", padx=(0, 12), pady=(2, 2))
        ttk.Button(params, text="Reset to defaults", command=self.reset_defaults).grid(
            row=(len(param_specs) // 4) + 1,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(8, 0),
        )

        right = ttk.Notebook(outer)
        right.grid(row=2, column=0, columnspan=2, sticky="nsew")

        log_frame = ttk.Frame(right, padding=8)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, wrap=tk.WORD, height=16)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_text.configure(
            font=("Consolas", 10),
        )
        self.log_text.configure(state=tk.DISABLED)
        right.add(log_frame, text="Run Log")

        summary_frame = ttk.Frame(right, padding=8)
        summary_frame.columnconfigure(0, weight=1)
        summary_frame.rowconfigure(0, weight=1)
        self.summary_text = tk.Text(summary_frame, wrap=tk.WORD)
        self.summary_text.grid(row=0, column=0, sticky="nsew")
        self.summary_text.configure(
            font=("Consolas", 10),
        )
        self.summary_text.configure(state=tk.DISABLED)
        right.add(summary_frame, text="Summary")

        trials_frame = ttk.Frame(right, padding=8)
        trials_frame.columnconfigure(0, weight=1)
        trials_frame.rowconfigure(0, weight=1)
        columns = ("phase", "trial", "soa", "response", "rt", "t50", "ci95")
        self.trials_table = ttk.Treeview(trials_frame, columns=columns, show="headings", height=16)
        headings = {
            "phase": "Phase",
            "trial": "Trial",
            "soa": "SOA (ms)",
            "response": "Response",
            "rt": "RT (ms)",
            "t50": "T50 (ms)",
            "ci95": "CI95 width",
        }
        widths = {
            "phase": 100,
            "trial": 70,
            "soa": 90,
            "response": 90,
            "rt": 90,
            "t50": 90,
            "ci95": 90,
        }
        for col in columns:
            self.trials_table.heading(col, text=headings[col])
            self.trials_table.column(col, width=widths[col], anchor=tk.CENTER)
        self.trials_table.grid(row=0, column=0, sticky="nsew")
        right.add(trials_frame, text="Trials")

        status_bar = ttk.Label(outer, textvariable=self.status_var, style="Status.TLabel", anchor="w")
        status_bar.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _set_summary_text(self, text: str) -> None:
        self.summary_text.configure(state=tk.NORMAL)
        self.summary_text.delete("1.0", tk.END)
        self.summary_text.insert(tk.END, text)
        self.summary_text.configure(state=tk.DISABLED)

    def _set_running(self, running: bool) -> None:
        self.start_button.configure(state=tk.DISABLED if running else tk.NORMAL)
        self.stop_button.configure(state=tk.NORMAL if running else tk.DISABLED)

    def _build_host_cmd(self, include_list_ports: bool = False) -> list[str]:
        if getattr(sys, "frozen", False):
            # In packaged mode run the same executable in host mode.
            cmd = [sys.executable, "--host-mode"]
        else:
            cmd = [sys.executable, str(PROJECT_ROOT / "src" / "tdt_host.py")]
        if include_list_ports:
            cmd.append("--list-ports")
            return cmd

        subject = self.subject_var.get().strip() or "test_subject"
        cmd.extend(["--subject", subject])

        port = self.port_var.get().strip()
        if port:
            cmd.extend(["--port", port])

        flash_value = self.flash_var.get().strip()
        if flash_value:
            cmd.extend(["--flash-level", flash_value])

        if self.skip_practice_var.get():
            cmd.append("--skip-practice")
        if self.skip_sanity_var.get():
            cmd.append("--skip-sanity")

        for flag, var in self.param_vars.items():
            value = var.get().strip()
            if value:
                cmd.extend([flag, value])
        return cmd

    def _validate_form(self) -> str | None:
        if self.flash_var.get().strip():
            try:
                value = int(self.flash_var.get().strip())
                if value < 0 or value > 255:
                    return "Flash level must be an integer from 0 to 255."
            except ValueError:
                return "Flash level must be an integer from 0 to 255."
        for flag, var in self.param_vars.items():
            value = var.get().strip()
            if not value:
                continue
            try:
                float(value)
            except ValueError:
                return f"Invalid value for {flag}: '{value}'"
        return None

    def reset_defaults(self) -> None:
        self.subject_var.set("test_subject")
        self.port_var.set("")
        self.flash_var.set("")
        self.skip_practice_var.set(False)
        self.skip_sanity_var.set(False)
        for flag, default_value in self.param_defaults.items():
            self.param_vars[flag].set(default_value)
        self.status_var.set("Defaults restored.")

    def start_session(self) -> None:
        if self._process is not None:
            return
        validation_error = self._validate_form()
        if validation_error is not None:
            messagebox.showerror("Invalid parameters", validation_error)
            return

        self._set_running(True)
        self.status_var.set("Running session...")
        self._append_log("")
        self._append_log("=== Starting session ===")
        self._append_log(" ".join(self._build_host_cmd()))

        def worker() -> None:
            try:
                cmd = self._build_host_cmd()
                process = subprocess.Popen(
                    cmd,
                    cwd=str(PROJECT_ROOT),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                self._process = process
                assert process.stdout is not None
                for line in process.stdout:
                    self._output_queue.put(("log", line.rstrip("\n")))
                exit_code = process.wait()
                self._output_queue.put(("done", str(exit_code)))
            except Exception as exc:
                self._output_queue.put(("error", str(exc)))

        self._worker_thread = threading.Thread(target=worker, daemon=True)
        self._worker_thread.start()

    def stop_session(self) -> None:
        if self._process is None:
            return
        try:
            self._process.terminate()
            self.status_var.set("Stopping session...")
            self._append_log("Stopping session...")
        except Exception as exc:
            messagebox.showerror("Stop failed", str(exc))

    def on_close(self) -> None:
        if self._process is not None:
            try:
                self._process.terminate()
            except Exception:
                pass
        self.root.destroy()

    def list_ports(self) -> None:
        self._append_log("")
        self._append_log("=== Listing serial ports ===")
        cmd = self._build_host_cmd(include_list_ports=True)
        try:
            completed = subprocess.run(
                cmd,
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except Exception as exc:
            messagebox.showerror("List ports failed", str(exc))
            return

        output = (completed.stdout or "").strip()
        if output:
            for line in output.splitlines():
                self._append_log(line)
        else:
            self._append_log("(no output)")

    def refresh_sessions(self) -> None:
        self.sessions_list.delete(0, tk.END)
        if not SESSIONS_DIR.exists():
            self.status_var.set(f"Sessions folder not found: {SESSIONS_DIR}")
            return

        self._session_paths = sorted(
            [path for path in SESSIONS_DIR.iterdir() if path.is_dir()],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in self._session_paths:
            self.sessions_list.insert(tk.END, path.name)
        self.status_var.set(f"Loaded {len(self._session_paths)} sessions.")

    def on_select_session(self, _event: Any = None) -> None:
        selection = self.sessions_list.curselection()
        if not selection:
            return
        index = int(selection[0])
        if index < 0 or index >= len(self._session_paths):
            return
        session_dir = self._session_paths[index]
        self._selected_session = session_dir
        self.load_session(session_dir)

    def load_session(self, session_dir: Path) -> None:
        try:
            summary, rows = tdtr.load_session(session_dir)
        except Exception as exc:
            messagebox.showerror("Load failed", f"Could not load session {session_dir.name}\n{exc}")
            return

        lines = tdtr.build_session_summary_lines(summary)
        self._set_summary_text("\n".join(lines))

        for item in self.trials_table.get_children():
            self.trials_table.delete(item)
        for row in rows:
            self.trials_table.insert(
                "",
                tk.END,
                values=(
                    row.get("phase", ""),
                    row.get("session_trial_number", ""),
                    row.get("requested_soa_ms", ""),
                    row.get("response", ""),
                    "" if row.get("rt_ms") is None else int(row["rt_ms"]),
                    "" if row.get("threshold50_ms") is None else f"{float(row['threshold50_ms']):.2f}",
                    "" if row.get("ci50_width_ms") is None else f"{float(row['ci50_width_ms']):.2f}",
                ),
            )
        self.status_var.set(f"Session loaded: {session_dir.name}")

    def open_selected_report(self) -> None:
        if self._selected_session is None:
            messagebox.showinfo("No session selected", "Select a session from the list first.")
            return
        report_path = self._selected_session / "session_report.html"
        if not report_path.exists():
            messagebox.showwarning("Report missing", f"Report file not found:\n{report_path}")
            return
        webbrowser.open(report_path.as_uri())

    def _poll_output_queue(self) -> None:
        while True:
            try:
                event_type, payload = self._output_queue.get_nowait()
            except queue.Empty:
                break

            if event_type == "log":
                self._append_log(payload)
            elif event_type == "done":
                self._append_log(f"=== Process finished with code {payload} ===")
                self._process = None
                self._set_running(False)
                if payload == "0":
                    self.status_var.set("Session finished successfully.")
                    self.refresh_sessions()
                    if self._session_paths:
                        newest = self._session_paths[0]
                        self._selected_session = newest
                        self.load_session(newest)
                else:
                    self.status_var.set(f"Session failed (exit code {payload}).")
            elif event_type == "error":
                self._append_log(f"ERROR: {payload}")
                self._process = None
                self._set_running(False)
                self.status_var.set("Session failed to start.")

        self.root.after(120, self._poll_output_queue)


def main() -> int:
    if "--host-mode" in sys.argv:
        host_args = [arg for arg in sys.argv[1:] if arg != "--host-mode"]
        return int(tdt_host.main(host_args))

    if sys.platform.startswith("win"):
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "tdt.studio.app"
            )
        except Exception:
            pass

    root = tk.Tk()
    app = TdtGuiApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
