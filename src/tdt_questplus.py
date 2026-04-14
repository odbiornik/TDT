from __future__ import annotations

from dataclasses import asdict, dataclass, field
import random
from statistics import NormalDist
from typing import Any

import numpy as np
import questplus as qp


@dataclass
class StopCriteria:
    threshold50_ci95_width_ms: float = 5.0
    mean_entropy_normalized: float | None = None
    minimum_sanity_correct_rate: float | None = 0.5


@dataclass
class SanityCheckConfig:
    enabled: bool = True
    first_after_adaptive_trials: int = 5
    interval_adaptive_trials: int = 5
    sequence: tuple[str, ...] = ("zero", "easy")
    zero_soa_ms: int = 0
    easy_target_probability: float = 0.90
    easy_uncertainty_min_margin_ms: float = 6.0
    easy_uncertainty_ci_fraction: float = 0.5
    easy_min_soa_ms: int = 50
    easy_max_soa_ms: int = 95


@dataclass
class ExperimentConfig:
    trial_count: int = 64
    practice_trial_count: int = 8
    practice_soa_ms: tuple[int, ...] = (0, 15, 30, 45, 60, 75, 90, 105)
    min_trials_for_early_stop: int = 32
    min_soa_ms: int = 0
    max_soa_ms: int = 100
    step_ms: int = 1
    flash_duration_ms: int = 10
    # Set LED stimulus brightness here (0-255). This value is sent from the host
    # to the Atom for each trial, so you can change it without reflashing firmware.
    flash_rgb_level: int = 255
    response_timeout_ms: int = 8000
    prestim_delay_ms: int = 500
    inter_trial_interval_ms: int = 1500
    # Threshold-first mode: keep the psychometric shape fixed enough that
    # QUEST+ focuses on the threshold instead of spending many late trials
    # on unstable slope/lapse estimation.
    sd_values_ms: tuple[int, ...] = (6,)
    lapse_values: tuple[float, ...] = (0.01,)
    monte_carlo_samples: int = 5000
    monte_carlo_seed: int = 12345
    stim_selection_method: str = "min_entropy"
    stim_selection_options: dict[str, Any] = field(default_factory=dict)
    stop_criteria: StopCriteria = field(default_factory=StopCriteria)
    sanity_checks: SanityCheckConfig = field(default_factory=SanityCheckConfig)

    def soa_values(self) -> np.ndarray:
        return np.arange(
            self.min_soa_ms,
            self.max_soa_ms + self.step_ms,
            self.step_ms,
            dtype=float,
        )

    @property
    def precision_window_half_width_ms(self) -> float:
        return self.stop_criteria.threshold50_ci95_width_ms / 2.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_staircase(config: ExperimentConfig) -> qp.QuestPlus:
    stim_domain = dict(intensity=config.soa_values())
    param_domain = dict(
        mean=config.soa_values().copy(),
        sd=np.asarray(config.sd_values_ms, dtype=float),
        lapse_rate=np.asarray(config.lapse_values, dtype=float),
    )

    kwargs = dict(
        stim_domain=stim_domain,
        func="norm_cdf_2",
        stim_scale="linear",
        param_domain=param_domain,
        outcome_domain=dict(response=["Yes", "No"]),
        stim_selection_method=config.stim_selection_method,
        param_estimation_method="mean",
    )

    stim_selection_options = dict(config.stim_selection_options)
    if stim_selection_options:
        stim_selection_options.setdefault("random_seed", config.monte_carlo_seed)

    try:
        if stim_selection_options:
            return qp.QuestPlus(
                **kwargs,
                stim_selection_options=stim_selection_options,
            )
        return qp.QuestPlus(**kwargs)
    except (TypeError, ValueError):
        # Older questplus versions may not support all selection options.
        kwargs["stim_selection_method"] = "min_entropy"
        return qp.QuestPlus(**kwargs)


def practice_schedule(config: ExperimentConfig) -> list[int]:
    values = list(config.practice_soa_ms)
    if not values or config.practice_trial_count <= 0:
        return []

    schedule: list[int] = []
    while len(schedule) < config.practice_trial_count:
        block = values.copy()
        random.shuffle(block)
        schedule.extend(block)
    return schedule[: config.practice_trial_count]


def should_run_sanity_trial(
    config: ExperimentConfig,
    *,
    completed_trials: int,
    last_trigger_completed_trials: int | None,
) -> bool:
    sanity = config.sanity_checks
    if not sanity.enabled:
        return False
    if completed_trials < sanity.first_after_adaptive_trials:
        return False
    if completed_trials % sanity.interval_adaptive_trials != 0:
        return False
    return last_trigger_completed_trials != completed_trials


def planned_easy_sanity_soa_ms(
    config: ExperimentConfig,
    *,
    summary: dict[str, Any],
) -> int:
    sanity = config.sanity_checks
    threshold50_ms = float(summary["estimates"]["threshold50_ms"])
    threshold75_ms = float(summary["estimates"]["threshold75_ms"])
    lapse_rate = float(summary["estimates"]["lapse_rate"])
    ci_width_ms = float(summary["reliability"]["threshold50_ci95_width_ms"])
    sd_ms = float(config.sd_values_ms[0])

    base_easy_ms = float(
        criterion_threshold_ms(
            threshold50_ms,
            sd_ms,
            lapse_rate,
            criterion=sanity.easy_target_probability,
        )
    )
    uncertainty_margin_ms = max(
        sanity.easy_uncertainty_min_margin_ms,
        ci_width_ms * sanity.easy_uncertainty_ci_fraction,
    )
    easy_target_ms = max(
        base_easy_ms,
        threshold75_ms + uncertainty_margin_ms,
        float(sanity.easy_min_soa_ms),
    )
    easy_target_ms = min(
        easy_target_ms,
        float(min(sanity.easy_max_soa_ms, config.max_soa_ms)),
    )
    easy_target_ms = max(easy_target_ms, float(config.min_soa_ms))
    return int(round(easy_target_ms / config.step_ms) * config.step_ms)


def next_sanity_trial_spec(
    config: ExperimentConfig,
    summary: dict[str, Any],
    *,
    sanity_trials_completed: int,
) -> dict[str, Any]:
    sanity = config.sanity_checks
    sequence = sanity.sequence or ("zero",)
    kind = sequence[sanity_trials_completed % len(sequence)]

    if kind == "zero":
        return dict(
            kind="zero",
            soa_ms=int(sanity.zero_soa_ms),
            expected_response="no",
            label="0 ms control",
        )

    easy_soa_ms = planned_easy_sanity_soa_ms(
        config,
        summary=summary,
    )
    return dict(
        kind="easy",
        soa_ms=int(easy_soa_ms),
        expected_response="yes",
        label="high-interval control",
    )


def build_trial_command(
    trial_id: int,
    soa_ms: int,
    config: ExperimentConfig,
    *,
    phase: str,
    lead_led: str = "simultaneous",
) -> dict[str, Any]:
    return dict(
        type="run_trial",
        trial_id=trial_id,
        phase=phase,
        soa_ms=int(soa_ms),
        lead_led=str(lead_led),
        flash_ms=int(config.flash_duration_ms),
        flash_rgb_level=int(config.flash_rgb_level),
        prestim_delay_ms=int(config.prestim_delay_ms),
        response_timeout_ms=int(config.response_timeout_ms),
    )


def inverse_normal_cdf(probabilities: np.ndarray | float) -> np.ndarray:
    values = np.asarray(probabilities, dtype=float)
    flat = [NormalDist().inv_cdf(float(prob)) for prob in values.reshape(-1)]
    return np.asarray(flat, dtype=float).reshape(values.shape)


def criterion_threshold_ms(
    mean_ms: np.ndarray | float,
    sd_ms: np.ndarray | float,
    lapse_rate: np.ndarray | float,
    *,
    criterion: float,
) -> np.ndarray:
    mean_ms = np.asarray(mean_ms, dtype=float)
    sd_ms = np.asarray(sd_ms, dtype=float)
    lapse_rate = np.asarray(lapse_rate, dtype=float)

    lower = lapse_rate
    upper = 1.0 - lapse_rate
    if np.any((criterion <= lower) | (criterion >= upper)):
        raise ValueError("Criterion must lie between the psychometric asymptotes.")

    normalized = (criterion - lower) / (upper - lower)
    normalized = np.clip(normalized, 1e-6, 1 - 1e-6)
    z_value = inverse_normal_cdf(normalized)
    return mean_ms + sd_ms * z_value


def extract_axis_and_probs(
    marginal_pdf: Any,
    *,
    fallback_axis: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    if hasattr(marginal_pdf, "dims") and hasattr(marginal_pdf, "coords"):
        dim_name = marginal_pdf.dims[0]
        axis = np.asarray(marginal_pdf.coords[dim_name].values, dtype=float)
        probs = np.asarray(marginal_pdf.values, dtype=float).reshape(-1)
    elif isinstance(marginal_pdf, dict) and {"x", "p"}.issubset(marginal_pdf):
        axis = np.asarray(marginal_pdf["x"], dtype=float)
        probs = np.asarray(marginal_pdf["p"], dtype=float).reshape(-1)
    else:
        probs = np.asarray(marginal_pdf, dtype=float).reshape(-1)
        if fallback_axis is None:
            axis = np.arange(probs.size, dtype=float)
        else:
            axis = np.asarray(fallback_axis, dtype=float).reshape(-1)

    if axis.size != probs.size:
        raise ValueError(
            f"Posterior axis length ({axis.size}) does not match probability length ({probs.size})."
        )

    probs = np.clip(probs, 0.0, None)
    total = float(np.sum(probs))
    if total == 0.0:
        raise ValueError("Posterior cannot be normalized because its probability mass is zero.")
    probs = probs / total
    return axis, probs


def weighted_mean(values: np.ndarray, probs: np.ndarray) -> float:
    return float(np.sum(values * probs))


def weighted_sd(values: np.ndarray, probs: np.ndarray) -> float:
    mean_value = weighted_mean(values, probs)
    variance = float(np.sum(probs * (values - mean_value) ** 2))
    return variance ** 0.5


def weighted_mode(values: np.ndarray, probs: np.ndarray) -> float:
    return float(values[np.argmax(probs)])


def weighted_quantile(
    values: np.ndarray,
    probs: np.ndarray,
    quantiles: float | list[float] | np.ndarray,
) -> np.ndarray:
    order = np.argsort(values)
    values = values[order]
    probs = probs[order]
    cumulative = np.cumsum(probs)
    cumulative = np.concatenate(([0.0], cumulative))
    values = np.concatenate(([values[0]], values))
    quantiles = np.atleast_1d(quantiles)
    return np.interp(quantiles, cumulative, values)


def entropy_bits(probs: np.ndarray) -> float:
    valid = probs[probs > 0]
    return float(-np.sum(valid * np.log2(valid)))


def posterior_mass_in_window(
    values: np.ndarray,
    probs: np.ndarray,
    center: float,
    half_width: float,
) -> float:
    mask = np.abs(values - center) <= half_width
    return float(np.sum(probs[mask]))


def approximate_threshold_distribution(
    staircase: qp.QuestPlus,
    *,
    criterion: float,
    n_samples: int,
    seed: int,
) -> np.ndarray:
    marginals = staircase.marginal_posterior
    mean_values, mean_probs = extract_axis_and_probs(
        marginals["mean"],
        fallback_axis=staircase.param_domain["mean"],
    )
    sd_values, sd_probs = extract_axis_and_probs(
        marginals["sd"],
        fallback_axis=staircase.param_domain["sd"],
    )
    lapse_values, lapse_probs = extract_axis_and_probs(
        marginals["lapse_rate"],
        fallback_axis=staircase.param_domain["lapse_rate"],
    )

    rng = np.random.default_rng(seed)
    mean_samples = rng.choice(mean_values, size=n_samples, p=mean_probs)
    sd_samples = rng.choice(sd_values, size=n_samples, p=sd_probs)
    lapse_samples = rng.choice(lapse_values, size=n_samples, p=lapse_probs)
    return criterion_threshold_ms(
        mean_samples,
        sd_samples,
        lapse_samples,
        criterion=criterion,
    )


def reliability_metrics(
    staircase: qp.QuestPlus,
    config: ExperimentConfig,
    *,
    seed: int,
) -> dict[str, float]:
    marginals = staircase.marginal_posterior
    mean_values, mean_probs = extract_axis_and_probs(
        marginals["mean"],
        fallback_axis=staircase.param_domain["mean"],
    )

    mean_ci95 = weighted_quantile(mean_values, mean_probs, [0.025, 0.975])
    mean_median = weighted_quantile(mean_values, mean_probs, 0.5)[0]
    mean_iqr = weighted_quantile(mean_values, mean_probs, [0.25, 0.75])
    mean_estimate = weighted_mean(mean_values, mean_probs)
    mean_entropy = entropy_bits(mean_probs)
    max_entropy = np.log2(mean_probs.size)

    threshold75_samples = approximate_threshold_distribution(
        staircase,
        criterion=0.75,
        n_samples=config.monte_carlo_samples,
        seed=seed,
    )
    threshold75_ci95 = np.quantile(threshold75_samples, [0.025, 0.975])

    return dict(
        threshold50_mean_ms=mean_estimate,
        threshold50_median_ms=float(mean_median),
        threshold50_mode_ms=weighted_mode(mean_values, mean_probs),
        threshold50_posterior_sd_ms=weighted_sd(mean_values, mean_probs),
        threshold50_iqr_ms=float(mean_iqr[1] - mean_iqr[0]),
        threshold50_ci95_low_ms=float(mean_ci95[0]),
        threshold50_ci95_high_ms=float(mean_ci95[1]),
        threshold50_ci95_width_ms=float(mean_ci95[1] - mean_ci95[0]),
        threshold50_mass_within_precision_window=posterior_mass_in_window(
            mean_values,
            mean_probs,
            center=mean_estimate,
            half_width=config.precision_window_half_width_ms,
        ),
        threshold75_ci95_low_ms=float(threshold75_ci95[0]),
        threshold75_ci95_high_ms=float(threshold75_ci95[1]),
        threshold75_ci95_width_ms=float(threshold75_ci95[1] - threshold75_ci95[0]),
        mean_entropy_bits=float(mean_entropy),
        mean_entropy_normalized=float(mean_entropy / max_entropy),
        mean_focus_score=float(1.0 - (mean_entropy / max_entropy)),
    )


def should_stop_early(
    metrics: dict[str, float],
    config: ExperimentConfig,
    completed_trials: int,
    sanity_summary: dict[str, Any] | None = None,
) -> tuple[bool, dict[str, bool]]:
    if completed_trials < config.min_trials_for_early_stop:
        return False, dict(enough_trials=False, threshold50=False, entropy=False, sanity=False)

    entropy_required = config.stop_criteria.mean_entropy_normalized is not None
    entropy_ok = True
    if entropy_required:
        entropy_ok = (
            metrics["mean_entropy_normalized"]
            <= float(config.stop_criteria.mean_entropy_normalized)
        )

    sanity_required = (
        config.sanity_checks.enabled
        and config.stop_criteria.minimum_sanity_correct_rate is not None
    )
    sanity_ok = True
    if sanity_required:
        if sanity_summary is None:
            sanity_ok = False
        else:
            total_valid = int(sanity_summary.get("valid_trials_total", 0))
            combined_rate = sanity_summary.get("correct_rate_total")
            sanity_ok = bool(
                total_valid > 0
                and combined_rate is not None
                and float(combined_rate) >= float(config.stop_criteria.minimum_sanity_correct_rate)
            )

    checks = dict(
        enough_trials=True,
        threshold50=(
            metrics["threshold50_ci95_width_ms"]
            <= config.stop_criteria.threshold50_ci95_width_ms
        ),
        entropy=entropy_ok,
        sanity=sanity_ok,
    )
    return all(checks.values()), checks


def summarize_staircase(
    staircase: qp.QuestPlus,
    config: ExperimentConfig,
    completed_trials: int,
    sanity_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    estimate = staircase.param_estimate
    threshold50_ms = float(estimate["mean"])
    sd_ms = float(estimate["sd"])
    lapse_rate = float(estimate["lapse_rate"])
    threshold25_ms = float(
        criterion_threshold_ms(threshold50_ms, sd_ms, lapse_rate, criterion=0.25)
    )
    threshold75_ms = float(
        criterion_threshold_ms(threshold50_ms, sd_ms, lapse_rate, criterion=0.75)
    )
    jnd_ms = (threshold75_ms - threshold25_ms) / 2.0

    metrics = reliability_metrics(
        staircase,
        config,
        seed=config.monte_carlo_seed + int(completed_trials),
    )
    early_stop, checks = should_stop_early(
        metrics,
        config,
        completed_trials,
        sanity_summary=sanity_summary,
    )

    return dict(
        completed_trials=int(completed_trials),
        estimates=dict(
            threshold50_ms=threshold50_ms,
            threshold75_ms=threshold75_ms,
            threshold25_ms=threshold25_ms,
            jnd_ms=jnd_ms,
            sd_ms=sd_ms,
            guess_rate=lapse_rate,
            lapse_rate=lapse_rate,
        ),
        reliability=metrics,
        early_stop=dict(active=early_stop, checks=checks),
    )


def outcome_from_trial_result(trial_result: dict[str, Any]) -> dict[str, str] | None:
    response = str(trial_result.get("response", "")).lower()
    if response == "yes":
        return dict(response="Yes")
    if response == "no":
        return dict(response="No")
    return None


def summarize_sanity_trials(
    sanity_trials: list[dict[str, Any]],
    config: ExperimentConfig,
) -> dict[str, Any]:
    def summarize_kind(kind: str, expected_response: str) -> dict[str, Any]:
        kind_trials = [trial for trial in sanity_trials if trial.get("sanity_kind") == kind]
        valid_trials = [
            trial
            for trial in kind_trials
            if str(trial.get("response", "")).lower() in {"yes", "no"}
        ]
        correct_trials = [
            trial
            for trial in valid_trials
            if str(trial.get("response", "")).lower() == expected_response
        ]
        return dict(
            trials=len(kind_trials),
            valid_trials=len(valid_trials),
            correct_trials=len(correct_trials),
            correct_rate=(
                float(len(correct_trials) / len(valid_trials))
                if valid_trials
                else None
            ),
        )

    zero_summary = summarize_kind("zero", "no")
    easy_summary = summarize_kind("easy", "yes")
    valid_trials_total = int(zero_summary["valid_trials"] + easy_summary["valid_trials"])
    correct_trials_total = int(zero_summary["correct_trials"] + easy_summary["correct_trials"])

    return dict(
        enabled=config.sanity_checks.enabled,
        total_trials=len(sanity_trials),
        valid_trials_total=valid_trials_total,
        correct_trials_total=correct_trials_total,
        correct_rate_total=(
            float(correct_trials_total / valid_trials_total)
            if valid_trials_total
            else None
        ),
        interval_adaptive_trials=config.sanity_checks.interval_adaptive_trials,
        first_after_adaptive_trials=config.sanity_checks.first_after_adaptive_trials,
        sequence=list(config.sanity_checks.sequence),
        zero_control=zero_summary,
        easy_control=easy_summary,
    )


def summary_lines(summary: dict[str, Any], config: ExperimentConfig) -> list[str]:
    estimates = summary["estimates"]
    reliability = summary["reliability"]
    checks = summary["early_stop"]["checks"]
    entropy_check = (
        "off"
        if config.stop_criteria.mean_entropy_normalized is None
        else str(checks["entropy"])
    )
    sanity_check = (
        "off"
        if (not config.sanity_checks.enabled or config.stop_criteria.minimum_sanity_correct_rate is None)
        else str(checks["sanity"])
    )

    lines = [
        f"Trials completed: {summary['completed_trials']}/{config.trial_count}",
        f"Flash brightness: {config.flash_rgb_level}/255",
        f"Threshold 50%: {estimates['threshold50_ms']:.1f} ms",
        f"Threshold 75%: {estimates['threshold75_ms']:.1f} ms",
        f"JND proxy: {estimates['jnd_ms']:.1f} ms",
        f"Guess rate: {estimates['guess_rate']:.1%}",
        f"Lapse rate: {estimates['lapse_rate']:.1%}",
        f"Threshold 50% CI95 width: {reliability['threshold50_ci95_width_ms']:.2f} ms",
        (
            "Posterior mass within "
            f"+/- {config.precision_window_half_width_ms:.1f} ms: "
            f"{reliability['threshold50_mass_within_precision_window']:.1%}"
        ),
        f"Normalized entropy: {reliability['mean_entropy_normalized']:.3f}",
        (
            "Early stop checks: "
            f"trials={checks['enough_trials']}, "
            f"ci50={checks['threshold50']}, "
            f"entropy={entropy_check}, "
            f"sanity={sanity_check}, "
            f"active={summary['early_stop']['active']}"
        ),
    ]

    sanity_summary = summary.get("sanity_checks")
    if sanity_summary:
        zero = sanity_summary["zero_control"]
        easy = sanity_summary["easy_control"]
        zero_rate = (
            "n/a"
            if zero["correct_rate"] is None
            else f"{zero['correct_rate']:.0%}"
        )
        easy_rate = (
            "n/a"
            if easy["correct_rate"] is None
            else f"{easy['correct_rate']:.0%}"
        )
        lines.append(
            "Sanity 0 ms controls (expected No): "
            f"{zero['correct_trials']}/{zero['valid_trials']} correct ({zero_rate})"
        )
        lines.append(
            "Sanity high-interval controls (expected Yes): "
            f"{easy['correct_trials']}/{easy['valid_trials']} correct ({easy_rate})"
        )
        total_rate = (
            "n/a"
            if sanity_summary["correct_rate_total"] is None
            else f"{sanity_summary['correct_rate_total']:.0%}"
        )
        lines.append(
            "Overall sanity accuracy: "
            f"{sanity_summary['correct_trials_total']}/{sanity_summary['valid_trials_total']} correct ({total_rate})"
        )

    return lines


def to_builtin(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): to_builtin(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_builtin(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value
