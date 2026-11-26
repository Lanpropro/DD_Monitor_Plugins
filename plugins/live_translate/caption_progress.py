"""Per-session draft/final ordering, independent from the caption surface."""


class CaptionProgress:
    def __init__(self):
        self.reset()

    def reset(self):
        self.sequence = (0, 0)
        self.final_segment = 0
        self.translated = ''

    def accept(self, result):
        phase = result.get('phase', 'final')
        segment = result.get('segment_id', 0)
        sequence = (segment, result.get('revision', 0))
        if segment and (sequence <= self.sequence or phase == 'draft' and segment <= self.final_segment):
            return None
        self.sequence = sequence
        if phase == 'draft':
            return result['text'] + ' …', self.translated
        self.final_segment = segment
        if result.get('event') == 'quality_review':
            return ()
        self.translated = result.get('translated', '')
        return result['text'], self.translated
