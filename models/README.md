# models/

Trained model weights go here (e.g. the motion model). This folder is
tracked by git, but weight files themselves are not — `.gitignore` already
excludes common formats (`*.pt`, `*.pth`, `*.onnx`, `*.safetensors`,
`*.h5`, `*.pkl`). Large binary files don't belong in git history; share
them via a drive link or similar and note that link here once you have
one.

**Status as of 2026-09-17:** empty — the trained motion model hasn't been
added yet. No loading code exists for it either, since that depends on
what framework it was trained with and what its inputs/outputs look like.
Once the file is here, add a short note in this README saying what it is,
then wire up a loader in `robot/` (or a new `motion/` folder if it's a
big enough piece on its own).
