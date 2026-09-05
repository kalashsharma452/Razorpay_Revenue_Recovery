"""
Recovery predictor.

Trains a classifier on data/train.csv and validates on data/validation.csv.
Exposes score() which returns P(recovery) for each candidate action
given an observable feature dict.

Supported model types:
  "lr"  — LogisticRegression (baseline, cannot learn action×context interactions)
  "gb"  — HistGradientBoostingClassifier (default, learns interactions natively)

ISOLATION RULE: This file never imports behavior_model.py.
It only sees the binary outcome column from the CSV.
"""

import os
import pickle
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder, StandardScaler
from sklearn.compose import ColumnTransformer

CANDIDATE_ACTIONS = [
    ("RETRY_LATER",          1),
    ("RETRY_LATER",          6),
    ("RETRY_LATER",          24),
    ("ALTERNATIVE_PAYMENT",  0),
    ("CUSTOMER_MESSAGE",     0),
]

NUMERIC_FEATURES = [
    "historical_success_rate",
    "historical_failure_count",
    "consecutive_failures",
    "total_orders",
    "customer_age_days",
    "previous_recovery_success",
    "attempt_number",
    "hour_of_day",
    "day_of_week",
    "delay_hours",
]

CATEGORICAL_FEATURES = [
    "root_cause",
    "payment_method",
    "amount_bucket",
    "action_type",
]

MODEL_PATH = os.path.join(os.path.dirname(__file__), "../../data/model.pkl")
LR_MODEL_PATH = os.path.join(os.path.dirname(__file__), "../../data/model_lr.pkl")


def selected_model_path() -> str:
    """Artifact chosen by DEMO_MODEL: 'gb' (default) or 'lr' (baseline demo)."""
    from app.config import DEMO_MODEL
    return LR_MODEL_PATH if DEMO_MODEL == "lr" else MODEL_PATH


@dataclass
class ValidationMetrics:
    roc_auc: float
    log_loss: float
    n_train: int
    n_val: int
    model_type: str


def artifact_metadata(model_path: str = MODEL_PATH) -> dict[str, str | int]:
    """Return a stable identity for the model artifact loaded by this process."""
    path = Path(model_path).resolve()
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(chunk)
    stat = path.stat()
    return {
        "model_path": str(path),
        "sha256": digest.hexdigest(),
        "size_bytes": stat.st_size,
        "mtime_utc": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
    }


def _build_lr_pipeline() -> Pipeline:
    from sklearn.preprocessing import OneHotEncoder
    preprocessor = ColumnTransformer([
        ("num", StandardScaler(), NUMERIC_FEATURES),
        ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL_FEATURES),
    ])
    return Pipeline([
        ("preprocessor", preprocessor),
        ("classifier", LogisticRegression(max_iter=1000, C=1.0, random_state=42)),
    ])


def _build_gb_pipeline() -> Pipeline:
    # HistGradientBoostingClassifier accepts ordinal-encoded categoricals natively
    # via categorical_features. We encode categoricals to integers then pass the
    # column indices to the classifier.
    n_num = len(NUMERIC_FEATURES)
    n_cat = len(CATEGORICAL_FEATURES)
    cat_indices = list(range(n_num, n_num + n_cat))

    preprocessor = ColumnTransformer([
        ("num", "passthrough", NUMERIC_FEATURES),
        ("cat", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1),
         CATEGORICAL_FEATURES),
    ])
    return Pipeline([
        ("preprocessor", preprocessor),
        ("classifier", HistGradientBoostingClassifier(
            max_iter=300,
            max_depth=6,
            learning_rate=0.05,
            min_samples_leaf=20,
            categorical_features=cat_indices,
            random_state=42,
        )),
    ])


def train(
    train_path: str = "data/train.csv",
    val_path: str = "data/validation.csv",
    model_path: str = MODEL_PATH,
    model_type: str = "gb",
) -> ValidationMetrics:
    train_df = pd.read_csv(train_path)
    val_df   = pd.read_csv(val_path)

    feature_cols = NUMERIC_FEATURES + CATEGORICAL_FEATURES
    X_train = train_df[feature_cols]
    y_train = train_df["recovered"].astype(int)
    X_val   = val_df[feature_cols]
    y_val   = val_df["recovered"].astype(int)

    pipeline = _build_gb_pipeline() if model_type == "gb" else _build_lr_pipeline()
    pipeline.fit(X_train, y_train)

    val_probs = pipeline.predict_proba(X_val)[:, 1]
    metrics = ValidationMetrics(
        roc_auc=round(roc_auc_score(y_val, val_probs), 4),
        log_loss=round(log_loss(y_val, val_probs), 4),
        n_train=len(train_df),
        n_val=len(val_df),
        model_type=model_type,
    )

    os.makedirs(os.path.dirname(model_path), exist_ok=True)
    with open(model_path, "wb") as f:
        pickle.dump(pipeline, f)

    print(f"[predictor] {model_type}  roc_auc={metrics.roc_auc}  log_loss={metrics.log_loss}")
    return metrics


class RecoveryPredictor:
    """Loaded once at app startup. Scores candidate actions for a given context."""

    def __init__(self, model_path: str = MODEL_PATH):
        with open(model_path, "rb") as f:
            self._pipeline = pickle.load(f)
        self.metrics: ValidationMetrics | None = None
        self.model_path = str(Path(model_path).resolve())

    def score(self, context: dict) -> dict[str, float]:
        """
        Return P(recovery) for each candidate action given observable features.

        context keys: NUMERIC_FEATURES (except delay_hours) + root_cause,
                      payment_method, amount_bucket.
        """
        rows = []
        action_keys = []
        for action_type, delay_hours in CANDIDATE_ACTIONS:
            rows.append({**context, "action_type": action_type, "delay_hours": delay_hours})
            action_keys.append((action_type, delay_hours))

        df = pd.DataFrame(rows)[NUMERIC_FEATURES + CATEGORICAL_FEATURES]
        probs = self._pipeline.predict_proba(df)[:, 1]

        return {
            f"{at}_{dh}h": float(p)
            for (at, dh), p in zip(action_keys, probs)
        }


# Module-level singleton
_predictor: RecoveryPredictor | None = None
_metrics: ValidationMetrics | None = None


def load(model_path: str | None = None) -> RecoveryPredictor:
    global _predictor
    _predictor = RecoveryPredictor(model_path or selected_model_path())
    return _predictor


def get() -> RecoveryPredictor | None:
    return _predictor


def loaded_model_key() -> str | None:
    """Key of the artifact currently loaded in the singleton, or None.

    Derived from the loaded artifact path (the actual model in memory), never
    from DEMO_MODEL or any current configuration. Used to stamp RecoveryActions
    at decision time so historical decisions keep their original identity.
    """
    if _predictor is None:
        return None
    loaded_path = Path(_predictor.model_path).resolve()
    return "lr" if loaded_path == Path(LR_MODEL_PATH).resolve() else "gb"


def get_metrics() -> ValidationMetrics | None:
    return _metrics


def train_and_load(
    train_path: str = "data/train.csv",
    val_path: str = "data/validation.csv",
    model_path: str = MODEL_PATH,
    model_type: str = "gb",
) -> RecoveryPredictor:
    global _metrics
    _metrics = train(train_path, val_path, model_path, model_type=model_type)
    return load(model_path)
