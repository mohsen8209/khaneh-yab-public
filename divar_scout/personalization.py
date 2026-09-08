from __future__ import annotations

import math
import statistics
import threading
from dataclasses import replace
from typing import Any

from .config import AppConfig
from .db import Database, utc_now
from .models import Listing, ScoreResult
from .neighborhoods import NeighborhoodAtlas, default_atlas, normalize_text
MODEL_VERSION = 5
BASE_FEATURES = (
    "larger_space",
    "two_bedrooms",
    "newer_building",
    "low_deposit",
    "low_rent",
    "unit_value",
    "neighborhood_quality",
    "balcony",
    "storage",
    "photo_coverage",
    "direct_location",
)

FEEDBACK_REASONS = {
    "positive": [
        {"id": "good_location", "label": "محله و موقعیت"},
        {"id": "good_value", "label": "قیمت مناسب"},
        {"id": "good_size", "label": "متراژ و تعداد خواب"},
        {"id": "good_age", "label": "سن بنا"},
        {"id": "good_amenities", "label": "امکانات"},
    ],
    "negative": [
        {"id": "bad_location", "label": "محله یا موقعیت"},
        {"id": "expensive", "label": "قیمت بالا"},
        {"id": "too_small", "label": "متراژ کم"},
        {"id": "too_old", "label": "سن بنا"},
        {"id": "suspicious", "label": "آگهی مشکوک"},
    ],
}

REASON_GUIDES: dict[str, tuple[tuple[str, float], ...]] = {
    "good_location": (("neighborhood_quality", 0.45),),
    "good_value": (("unit_value", 0.45),),
    "good_size": (("larger_space", 0.45),),
    "good_age": (("newer_building", 0.45),),
    "good_amenities": (("balcony", 0.3), ("storage", 0.3)),
    "bad_location": (("neighborhood_quality", 0.45),),
    "expensive": (("unit_value", 0.35), ("low_rent", 0.25)),
    "too_small": (("larger_space", 0.45),),
    "too_old": (("newer_building", 0.45),),
}


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _score_from_dict(payload: dict[str, Any] | None) -> ScoreResult | None:
    if not payload:
        return None
    allowed = ScoreResult.__dataclass_fields__
    return ScoreResult(**{key: value for key, value in payload.items() if key in allowed})


def _base_score(score: ScoreResult) -> ScoreResult:
    return replace(
        score,
        total=score.base_total if score.base_total is not None else score.total,
        base_total=None,
        personal_adjustment=0.0,
        personal_general_adjustment=0.0,
        personal_confidence=0,
        personalized=False,
        personal_reasons=[],
    )


class PersonalizationEngine:
    def __init__(
        self,
        config: AppConfig,
        database: Database,
        atlas: NeighborhoodAtlas | None = None,
        user_id: int | None = None,
    ) -> None:
        self.config = config
        self.database = database
        self.atlas = atlas or default_atlas()
        self.user_id = user_id
        self._lock = threading.RLock()
        self._model: dict[str, Any] | None = None
        self._signature: str | None = None

    def retrain(self) -> dict[str, Any]:
        with self._lock:
            signature = self.database.personalization_signature(user_id=self.user_id)
            model = self._train_model()
            self.database.save_personalization_model(
                model, signature, user_id=self.user_id
            )
            self._model = model
            self._signature = signature
            return self.public_state(model)

    def state(self) -> dict[str, Any]:
        return self.public_state(self._ensure_model())

    def public_state(self, model: dict[str, Any]) -> dict[str, Any]:
        keys = {
            "version", "stage", "trained", "signals", "feedback_count",
            "comparison_count", "training_pairs", "maturity", "progress",
            "activation_signals", "full_strength_signals", "remaining_to_activation",
            "top_preferences", "updated_at",
        }
        state = {key: model.get(key) for key in keys}
        state["learning_continues"] = True
        state["top_preferences"] = (model.get("top_preferences") or [])[:4]
        state["enabled"] = bool(self.config.personalization.get("enabled", True))
        state["reason_options"] = FEEDBACK_REASONS
        return state

    def apply(self, listing: Listing, score: ScoreResult) -> ScoreResult:
        base = _base_score(score)
        base_total = base.total
        model = self._ensure_model()
        if not self.config.personalization.get("enabled", True):
            return replace(base, base_total=base_total)

        max_adjustment = float(self.config.personalization["max_adjustment"])
        maturity = float(model.get("maturity") or 0)
        general_adjustment = 0.0
        general_confidence = 0
        reasons: list[str] = []
        features = self.features(listing, base)
        if model.get("feature_names") and maturity > 0:
            model_utility = self._utility(features, model)
            relative_utility = model_utility - float(model.get("utility_center") or 0)
            general_probability = 1 / (1 + math.exp(-_clamp(relative_utility, -20, 20)))
            general_adjustment = (
                (general_probability * 2 - 1) * max_adjustment * maturity
            )
            district_key = self._district_feature(listing.district)
            district_count = int(model.get("district_counts", {}).get(district_key, 0))
            familiarity = 0.75 + 0.25 * min(1.0, district_count / 3)
            general_confidence = int(round(maturity * familiarity * 100))
            reasons.extend(self._personal_reasons(features, model, general_adjustment))

        general_adjustment = round(general_adjustment, 2)
        adjustment = round(_clamp(general_adjustment, -max_adjustment, max_adjustment), 2)
        personalized_total = round(_clamp(base_total + adjustment, 0, 20), 1)
        status = self._personalized_status(listing, base, personalized_total)
        return replace(
            base,
            total=personalized_total,
            status=status,
            base_total=base_total,
            personal_adjustment=adjustment,
            personal_general_adjustment=general_adjustment,
            personal_confidence=general_confidence,
            personalized=abs(adjustment) >= 0.05,
            personal_reasons=reasons[:4],
        )

    def personalize_payload(
        self, listing_payload: dict[str, Any], score_payload: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        score = _score_from_dict(score_payload)
        if score is None:
            return None
        listing_fields = Listing.__dataclass_fields__
        listing = Listing(
            **{key: value for key, value in listing_payload.items() if key in listing_fields}
        )
        return self.apply(listing, score).as_dict()

    def features(
        self, listing: Listing, score: ScoreResult | dict[str, Any] | None = None
    ) -> dict[str, float]:
        profile = self.config.profile
        size = listing.size or profile["min_size"]
        age = listing.age if listing.age is not None else 15
        deposit = listing.deposit if listing.deposit is not None else profile["ideal_deposit"]
        rent = listing.rent if listing.rent is not None else profile["ideal_rent"]
        rate = float(profile["deposit_to_monthly_rate"])
        ideal_effective = profile["ideal_rent"] + profile["ideal_deposit"] * rate
        effective = rent + deposit * rate
        ideal_unit = ideal_effective / profile["min_size"]
        unit_cost = effective / max(1, size)
        rating = self.atlas.rate(listing.district)

        values = {
            "larger_space": _clamp((size - profile["min_size"]) / 60, -0.5, 1.0),
            "two_bedrooms": 1.0 if (listing.rooms or 0) >= 2 else 0.0,
            "newer_building": _clamp(1 - age / 30, 0, 1),
            "low_deposit": _clamp(1 - deposit / profile["ideal_deposit"], -1, 1),
            "low_rent": _clamp(1 - rent / profile["ideal_rent"], -1, 1),
            "unit_value": _clamp(1 - unit_cost / ideal_unit, -1, 1),
            "neighborhood_quality": _clamp(rating.score / 8, 0, 1),
            "balcony": 1.0 if listing.has_balcony else 0.0,
            "storage": 1.0 if listing.has_storage else 0.0,
            "photo_coverage": _clamp(listing.image_count / 10, 0, 1),
            "direct_location": 0.0 if listing.approximate_location else 1.0,
        }
        if listing.district:
            values[self._district_feature(listing.district)] = 1.0
        return values

    def select_comparison(
        self,
        records: list[dict[str, Any]],
        excluded_pairs: set[frozenset[str]] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], str] | None:
        compared = self.database.compared_pairs(user_id=self.user_id).union(
            excluded_pairs or set()
        )
        eligible = [
            item for item in records
            if item.get("score")
            and item["score"].get("status") not in {"rejected", "new"}
            and item.get("feedback") != "rejected"
            and item.get("detail_fetched_at")
        ][:50]
        if len(eligible) < 2:
            return None

        model = self._ensure_model()
        prepared: list[tuple[dict[str, Any], dict[str, float]]] = []
        for item in eligible:
            listing = Listing(**{
                key: value for key, value in item.items()
                if key in Listing.__dataclass_fields__
            })
            prepared.append((item, self.features(listing, item.get("score"))))

        best_general: tuple[float, dict[str, Any], dict[str, Any]] | None = None
        for index, (left, left_features) in enumerate(prepared):
            for right, right_features in prepared[index + 1:]:
                if frozenset((left["token"], right["token"])) in compared:
                    continue
                left_score = float(left["score"].get("total") or 0)
                right_score = float(right["score"].get("total") or 0)
                closeness = max(0.0, 2.5 - abs(left_score - right_score))
                feature_difference = sum(
                    abs(left_features.get(name, 0) - right_features.get(name, 0))
                    for name in BASE_FEATURES
                )
                uncertainty = closeness
                if model.get("trained"):
                    utility_gap = abs(
                        self._utility(left_features, model)
                        - self._utility(right_features, model)
                    )
                    uncertainty = 3 / (1 + utility_gap)
                unlabeled_bonus = int(not left.get("feedback")) + int(not right.get("feedback"))
                pair_score = uncertainty * 3 + feature_difference + unlabeled_bonus
                if best_general is None or pair_score > best_general[0]:
                    best_general = (pair_score, left, right)
        if best_general:
            return best_general[1], best_general[2], "general"
        return None

    def _ensure_model(self) -> dict[str, Any]:
        with self._lock:
            signature = self.database.personalization_signature(user_id=self.user_id)
            if self._model is not None and self._signature == signature:
                return self._model
            stored = self.database.load_personalization_model(user_id=self.user_id)
            if stored and stored[1] == signature and stored[0].get("version") == MODEL_VERSION:
                self._model, self._signature = stored[0], signature
                return self._model
            self.retrain()
            return self._model or self._empty_model()

    def _train_model(self) -> dict[str, Any]:
        feedback = self.database.list_feedback(user_id=self.user_id)
        comparisons = self.database.list_comparisons(user_id=self.user_id)
        general_feedback = feedback
        general_comparisons = comparisons
        signals = len(general_feedback) + len(general_comparisons)
        activation = int(self.config.personalization["activation_signals"])
        full_strength = int(self.config.personalization["full_strength_signals"])

        positive = [
            item for item in general_feedback
            if item["choice"] in {"favorite", "visit"}
        ]
        negative = [
            item for item in general_feedback if item["choice"] == "rejected"
        ]
        raw_pairs: list[tuple[dict[str, Any], dict[str, Any], float]] = []
        for preferred in positive:
            for rejected in negative:
                weight = 1.35 if preferred["choice"] == "visit" else 1.0
                raw_pairs.append((preferred, rejected, weight))
        for comparison in general_comparisons:
            if comparison["winner_token"] == comparison["left_token"]:
                winner = {"listing": comparison["left"], "score": comparison["left_score"]}
                loser = {"listing": comparison["right"], "score": comparison["right_score"]}
            else:
                winner = {"listing": comparison["right"], "score": comparison["right_score"]}
                loser = {"listing": comparison["left"], "score": comparison["left_score"]}
            raw_pairs.append((winner, loser, 2.0))

        reason_guides = [
            guide
            for item in general_feedback
            for reason in item["reasons"]
            for guide in REASON_GUIDES.get(reason, ())
        ]

        model = self._empty_model(
            signals=signals,
            feedback_count=len(general_feedback),
            comparison_count=len(general_comparisons),
            training_pairs=len(raw_pairs) + len(reason_guides),
        )
        if not raw_pairs and not reason_guides:
            return model

        prepared: list[tuple[dict[str, float], dict[str, float], float]] = []
        item_features: list[dict[str, float]] = []
        districts: dict[str, int] = {}
        for winner, loser, weight in raw_pairs[:500]:
            winner_score = _score_from_dict(winner.get("score"))
            loser_score = _score_from_dict(loser.get("score"))
            winner_listing = Listing(**winner["listing"])
            loser_listing = Listing(**loser["listing"])
            winner_features = self.features(
                winner_listing, _base_score(winner_score) if winner_score else None
            )
            loser_features = self.features(
                loser_listing, _base_score(loser_score) if loser_score else None
            )
            prepared.append((winner_features, loser_features, weight))
            item_features.extend((winner_features, loser_features))
            for current in (winner_listing, loser_listing):
                key = self._district_feature(current.district)
                districts[key] = districts.get(key, 0) + 1
        for item in general_feedback:
            item_score = _score_from_dict(item.get("score"))
            current = Listing(**item["listing"])
            current_features = self.features(
                current, _base_score(item_score) if item_score else None
            )
            item_features.append(current_features)
            key = self._district_feature(current.district)
            districts[key] = districts.get(key, 0) + 1
        if not item_features or (not prepared and not reason_guides):
            return model

        feature_names = list(BASE_FEATURES)
        district_features = sorted(
            {name for values in item_features for name in values if name.startswith("district::")}
        )
        feature_names.extend(district_features)
        rows: list[list[float]] = []
        labels: list[int] = []
        weights: list[float] = []
        for winner, loser, weight in prepared:
            difference = [winner.get(name, 0) - loser.get(name, 0) for name in feature_names]
            rows.extend((difference, [-value for value in difference]))
            labels.extend((1, 0))
            weights.extend((weight, weight))
        for feature, direction in reason_guides:
            difference = [direction if name == feature else 0.0 for name in feature_names]
            rows.extend((difference, [-value for value in difference]))
            labels.extend((1, 0))
            weights.extend((0.35, 0.35))

        scales = [
            max(0.1, statistics.pstdev(item.get(name, 0) for item in item_features))
            for name in feature_names
        ]
        totals = [0.0 for _name in feature_names]
        total_weight = 0.0
        for row, label, weight in zip(rows, labels, weights):
            direction = 1 if label else -1
            for index, value in enumerate(row):
                totals[index] += direction * weight * value / scales[index]
            total_weight += weight
        regularization = max(
            0.05, float(self.config.personalization.get("regularization_c", 0.3))
        )
        coefficients = [
            _clamp(value / max(total_weight, 1.0) * regularization * 4, -2.5, 2.5)
            for value in totals
        ]
        baselines = [
            float(statistics.median(item.get(name, 0) for item in item_features))
            for name in feature_names
        ]
        utilities = [
            sum(
                coefficient * item.get(name, 0) / scale
                for name, coefficient, scale in zip(feature_names, coefficients, scales)
            )
            for item in item_features
        ]

        maturity = self._maturity(signals, activation, full_strength)
        model.update(
            {
                "trained": True,
                "stage": self._stage(signals, activation, full_strength),
                "maturity": round(maturity, 4),
                "feature_names": feature_names,
                "coefficients": coefficients,
                "scales": scales,
                "feature_baselines": baselines,
                "utility_center": float(statistics.median(utilities)),
                "district_counts": districts,
                "top_preferences": self._top_preferences(feature_names, coefficients),
                "updated_at": utc_now(),
            }
        )
        return model

    def _empty_model(
        self,
        *,
        signals: int = 0,
        feedback_count: int = 0,
        comparison_count: int = 0,
        training_pairs: int = 0,
    ) -> dict[str, Any]:
        activation = int(self.config.personalization["activation_signals"])
        full_strength = int(self.config.personalization["full_strength_signals"])
        return {
            "version": MODEL_VERSION,
            "trained": False,
            "stage": self._stage(signals, activation, full_strength),
            "signals": signals,
            "feedback_count": feedback_count,
            "comparison_count": comparison_count,
            "training_pairs": training_pairs,
            "maturity": 0.0,
            "progress": int(round(min(1, signals / full_strength) * 100)),
            "activation_signals": activation,
            "full_strength_signals": full_strength,
            "remaining_to_activation": max(0, activation - signals),
            "top_preferences": [],
            "updated_at": None,
            "feature_names": [],
            "coefficients": [],
            "scales": [],
            "feature_baselines": [],
            "utility_center": 0.0,
            "district_counts": {},
        }

    @staticmethod
    def _maturity(signals: int, activation: int, full_strength: int) -> float:
        if signals < activation:
            return 0.0
        return _clamp((signals - activation + 1) / (full_strength - activation + 1), 0, 1)

    @staticmethod
    def _stage(signals: int, activation: int, full_strength: int) -> str:
        if signals < activation:
            return "collecting"
        if signals < full_strength:
            return "calibrating"
        return "active"

    @staticmethod
    def _district_feature(district: str | None) -> str:
        return "district::" + normalize_text(district or "unknown")

    @staticmethod
    def _utility(features: dict[str, float], model: dict[str, Any]) -> float:
        return sum(
            coefficient * features.get(name, 0) / (scale or 1)
            for name, coefficient, scale in zip(
                model.get("feature_names", []),
                model.get("coefficients", []),
                model.get("scales", []),
            )
        )

    def _personal_reasons(
        self,
        features: dict[str, float],
        model: dict[str, Any],
        adjustment: float,
    ) -> list[str]:
        contributions = []
        for name, coefficient, scale, baseline in zip(
            model.get("feature_names", []),
            model.get("coefficients", []),
            model.get("scales", []),
            model.get("feature_baselines", []),
        ):
            contribution = coefficient * (features.get(name, 0) - baseline) / (scale or 1)
            if abs(contribution) >= 0.04:
                contributions.append((contribution, name))
        preferred_sign = 1 if adjustment >= 0 else -1
        aligned = [item for item in contributions if item[0] * preferred_sign > 0]
        aligned.sort(key=lambda item: abs(item[0]), reverse=True)
        prefix = "امتیاز شخصی بالاتر" if adjustment >= 0 else "امتیاز شخصی پایین‌تر"
        return [f"{prefix}: {self._feature_label(name, contribution)}" for contribution, name in aligned[:2]]

    def _top_preferences(
        self, feature_names: list[str], coefficients: list[float]
    ) -> list[dict[str, Any]]:
        ranked = sorted(
            zip(feature_names, coefficients), key=lambda item: abs(item[1]), reverse=True
        )
        return [
            {
                "feature": name,
                "direction": "positive" if coefficient >= 0 else "negative",
                "label": self._preference_label(name, coefficient),
                "strength": round(abs(coefficient), 3),
            }
            for name, coefficient in ranked[:4]
            if abs(coefficient) >= 0.03
        ]

    @staticmethod
    def _feature_label(name: str, contribution: float) -> str:
        if name.startswith("district::"):
            return "محله با انتخاب‌های قبلی تو هم‌راستاست" if contribution > 0 else "محله کمتر با انتخاب‌هایت جور است"
        labels = {
            "larger_space": "متراژ",
            "two_bedrooms": "تعداد خواب",
            "newer_building": "سن بنا",
            "low_deposit": "میزان ودیعه",
            "low_rent": "میزان اجاره",
            "unit_value": "ارزش نسبت به متراژ",
            "neighborhood_quality": "کیفیت محله",
            "balcony": "بالکن",
            "storage": "انباری",
            "photo_coverage": "پوشش عکس‌ها",
            "direct_location": "دقت موقعیت",
        }
        return labels.get(name, name)

    def _preference_label(self, name: str, coefficient: float) -> str:
        positive = coefficient >= 0
        if name.startswith("district::"):
            district = name.split("::", 1)[1]
            return ("تمایل بیشتر به " if positive else "تمایل کمتر به ") + district
        labels = {
            "larger_space": ("متراژ بیشتر", "خانه جمع‌وجورتر"),
            "two_bedrooms": ("دو خواب", "یک خواب"),
            "newer_building": ("بنای کم‌سن‌تر", "سن بنا کم‌اهمیت‌تر"),
            "low_deposit": ("ودیعه کمتر", "ودیعه بیشتر با اجاره کمتر"),
            "low_rent": ("اجاره کمتر", "اجاره بیشتر با ودیعه کمتر"),
            "unit_value": ("ارزش بهتر هر متر", "قیمت هر متر کم‌اهمیت‌تر"),
            "neighborhood_quality": ("محله قوی‌تر", "محله اقتصادی‌تر"),
            "balcony": ("بالکن", "بالکن کم‌اهمیت‌تر"),
            "storage": ("انباری", "انباری کم‌اهمیت‌تر"),
            "photo_coverage": ("عکس‌های کامل‌تر", "تعداد عکس کم‌اهمیت‌تر"),
            "direct_location": ("پین دقیق‌تر", "پین تقریبی قابل‌قبول‌تر"),
        }
        pair = labels.get(name, (name, name))
        return pair[0] if positive else pair[1]

    def _personalized_status(
        self, listing: Listing, score: ScoreResult, personalized_total: float
    ) -> str:
        if score.status in {"rejected", "review"}:
            return score.status
        scoring = self.config.scoring
        balanced = (
            score.value >= scoring.get("minimum_alert_value_score", 3.5)
            and score.property >= scoring.get("minimum_alert_property_score", 2.0)
        )
        profile = self.config.profile
        over_hard_budget = (
            (listing.deposit or 0) > profile["hard_max_deposit"]
            or (listing.rent or 0) > profile["hard_max_rent"]
        )
        if over_hard_budget:
            exceptional = (
                personalized_total >= scoring["minimum_exceptional_budget_score"]
                and score.trust >= 85
                and score.location >= 6.5
                and balanced
            )
            return "alert" if exceptional else "review"
        return (
            "alert"
            if personalized_total >= scoring["minimum_alert_score"] and balanced
            else "candidate"
        )
