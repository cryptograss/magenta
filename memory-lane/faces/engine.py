"""Finding faces in a picture and telling whose they are, by OpenCV's open models.

YuNet finds the faces (with five landmarks each); SFace turns each one, aligned
by those landmarks, into a 128-number fingerprint. Two fingerprints of the same
person point the same way: their cosine similarity is high. Both models are
OpenCV's own (opencv_zoo, Apache-2.0), so anyone can run this, for anything.

MODEL names the pair. A fingerprint is only comparable with one made by the
same model, so every stored one carries it, and a change of model means
fingerprinting the reference photos again (store.Store.stale).
"""

import os

import cv2
import numpy as np

MODEL = 'opencv-yunet-2023mar+sface-2021dec'
MODELS_DIR = os.environ.get('FACES_MODELS_DIR', os.path.join(os.path.dirname(__file__), 'models'))
DETECTOR_FILE = 'face_detection_yunet_2023mar.onnx'
RECOGNIZER_FILE = 'face_recognition_sface_2021dec.onnx'

# OpenCV's own figure for SFace: cosine similarity at or above this, the same person.
SAME_PERSON = 0.363
# A face smaller than this many pixels across is too small to tell anyone by.
MIN_FACE = 40
# Pictures are looked at no larger than this along their longer side: a phone's
# 4000-pixel photo is 16 times the work for faces no easier to tell apart.
LONGEST_SIDE = 1600


class Engine:
    def __init__(self, models_dir=MODELS_DIR):
        self.detector = cv2.FaceDetectorYN.create(os.path.join(models_dir, DETECTOR_FILE), '', (320, 320),
                                                  score_threshold=0.8, nms_threshold=0.3, top_k=500)
        self.recognizer = cv2.FaceRecognizerSF.create(os.path.join(models_dir, RECOGNIZER_FILE), '')

    def look(self, data):
        """The faces in an image's bytes: (width, height, faces), or None if it isn't an image.
        Each face is {'box': [x, y, w, h], 'score', 'fingerprint'}, in the picture's own pixels, largest first."""
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return None
        h, w = image.shape[:2]
        scale = min(1.0, LONGEST_SIDE / max(h, w))
        small = image if scale == 1 else cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
        found = self.faces(small)
        for f in found:
            f['box'] = [round(v / scale) for v in f['box']]
        return w, h, found

    def faces(self, image):
        """Every face big enough to tell in a BGR picture: [{'box', 'score', 'fingerprint'}], largest first."""
        h, w = image.shape[:2]
        self.detector.setInputSize((w, h))
        _, found = self.detector.detect(image)
        if found is None:
            return []
        out = []
        for row in found:
            x, y, fw, fh = (float(v) for v in row[:4])
            if min(fw, fh) < MIN_FACE:
                continue
            aligned = self.recognizer.alignCrop(image, row)
            fingerprint = self.recognizer.feature(aligned).flatten().astype(np.float32)
            fingerprint /= np.linalg.norm(fingerprint) or 1.0
            out.append({'box': [round(x), round(y), round(fw), round(fh)], 'score': round(float(row[14]), 3),
                        'fingerprint': fingerprint})
        out.sort(key=lambda f: -f['box'][2] * f['box'][3])
        return out


def similarity(a, b):
    """Cosine similarity of two fingerprints (each already of length 1)."""
    return float(np.dot(a, b))
