# faces: whose faces are in a picture (faces/server.py). Built from the repository's root:
#   docker build -f faces/Dockerfile .
FROM python:3.12-slim

WORKDIR /app
COPY faces/requirements.txt faces/requirements.txt
RUN pip install --no-cache-dir -r faces/requirements.txt

# The two models, OpenCV's own (Apache-2.0), from opencv_zoo at a fixed commit and
# checked against their sha256: the same files on every build, or no build.
ENV FACES_MODELS_DIR=/app/faces/models
RUN python - <<'PY'
import hashlib, os, urllib.request
zoo = 'https://github.com/opencv/opencv_zoo/raw/47534e27c9851bb1128ccc0102f1145e27f23f98/models/'
models = {
    'face_detection_yunet/face_detection_yunet_2023mar.onnx': '8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4',
    'face_recognition_sface/face_recognition_sface_2021dec.onnx': '0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79',
}
os.makedirs(os.environ['FACES_MODELS_DIR'], exist_ok=True)
for path, sha in models.items():
    data = urllib.request.urlopen(zoo + path, timeout=120).read()
    assert hashlib.sha256(data).hexdigest() == sha, f'{path}: not the file expected'
    open(os.path.join(os.environ['FACES_MODELS_DIR'], os.path.basename(path)), 'wb').write(data)
PY

COPY faces faces
# It tells the test portraits apart, or the image isn't made.
RUN python -m unittest faces.tests.test_faces

VOLUME /data
EXPOSE 8090
CMD ["python", "-m", "faces.server"]
