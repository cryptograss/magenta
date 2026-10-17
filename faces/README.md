# faces

Whose faces are in a picture, from reference photos on PickiPedia. One small
service, for any Mood (magenta) and for PickiPedia, running on hunter.

- **References** are files on PickiPedia carrying `{{Face reference|Person}}`
  (Category:Face reference), where Person is that person's page. Every ten
  minutes, and on `POST /sync`, the service reads the category, fingerprints new
  and changed photos, and forgets removed ones.
- **`POST /match`** with a picture as the body answers its faces: where each is,
  whose it is if it's like enough to a reference, and the nearest few.
- **`GET /export`** gives every fingerprint with whose it is and the model that
  made it, as JSON.

The photos on PickiPedia are the record. The fingerprints here can always be
made again from them, by this model or a better one.

**Models:** OpenCV's YuNet (finds faces) and SFace (fingerprints them), from
opencv_zoo, Apache-2.0. A different model means a new `engine.MODEL`; the store
then fingerprints the references again.

**Key:** everything but `/health` needs `Authorization: Bearer $FACES_KEY`. It
comes from the vault (`faces_api_key`); without it the service won't start.

Run the tests from the repository's root, with the models in
`$FACES_MODELS_DIR`: `python -m unittest faces.tests.test_faces`. The image runs
them as it's built.
