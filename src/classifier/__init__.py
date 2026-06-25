"""src/classifier — NLP-powered article classification pipeline."""
from src.classifier.engine import ArticleClassifier
from src.classifier.entities import UAE_ENTITIES, ARAB_ENTITIES, GLOBAL_ENTITIES

__all__ = ["ArticleClassifier", "UAE_ENTITIES", "ARAB_ENTITIES", "GLOBAL_ENTITIES"]
