"""Suspicion signals only; a heuristic is never grounds to delete spoken text."""
import re


def normalised(text):
    return re.sub(r'[\W_]+', '', text).casefold()


def repetitive(text):
    return bool(re.search(r'(.{3,40}?)\1{2,}', normalised(text)))


def recognition_issues(text, seconds, previous=()):
    value = normalised(text)
    if not value:
        return ['empty_recognition']
    reasons = []
    if seconds >= 3 and len(value) <= 3:
        reasons.append('long_audio_short_text')
    if repetitive(text):
        reasons.append('repeated_recognition')
    if len(value) >= 8 and len(previous) >= 2 and all(normalised(item) == value for item in previous[-2:]):
        reasons.append('repeated_segment')
    return reasons
