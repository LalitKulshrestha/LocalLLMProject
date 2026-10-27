# LocalForge

LocalForge is a single-user, localhost-only media generation control plane for:

- face image + target video -> face-swapped video (FaceFusion)
- text -> image (ComfyUI)
- image + prompt -> video (ComfyUI)
- text -> video (ComfyUI)

All uploaded media, prompts, job metadata, and generated files remain on this
machine. LocalForge does not call a hosted AI or moderation API.

## Architecture

```text
Browser (127.0.0.1:8787)
          |
          v
 FastAPI control plane ---- SQLite job database
          |                         |
          v                         v
    Background worker ------ workspace/data
          |
          +---- FaceFusion CLI (face swap)
          +---- ComfyUI HTTP API (image/video generation)
```

The control plane binds to `127.0.0.1` by default. Do not expose it to a LAN or
the internet without adding authentication and TLS.

## Quick start

### 1. Bootstrap LocalForge

```bash
./scripts/bootstrap.sh
cp .env.example .env
./scripts/start.sh
```

Open <http://127.0.0.1:8787>.

### 2. Install engines

The bootstrap script can install just LocalForge, or install the two engines:

```bash
./scripts/install-engines.sh
```

This creates:

- `engines/facefusion` and a dedicated Python virtual environment
- `engines/ComfyUI` and a dedicated Python virtual environment

Engine downloads and model downloads are large. AlphaFace and FaceFusion's
supporting models are already installed. ComfyUI model files must be placed in
its `models/` folders.
Text-to-image requires the installed SD 1.5 checkpoint and a running ComfyUI
server. Video generation is disabled until a genuine video model and a tested
workflow are installed. The face-swap tab uses its separate FaceFusion models.

Video generation uses platform presets rather than width and height fields:
**Instagram Reels / YouTube Shorts (9:16)** and **YouTube video (16:9)**.
Local ComfyUI workflows receive model-friendly sizes of 576×1024 and
1024×576 respectively. The actual model and workflow determine the quality
and clip length. A long YouTube video must be assembled from shorter clips;
selecting 16:9 alone does not generate a long video. Face swap keeps the
target video's aspect ratio, so use a 9:16 target for a Reel or Short and a
16:9 target for a landscape YouTube video.

Seedance, Google Gemini Omni Flash / Veo, and Kling are hosted video services,
not models currently installed in LocalForge. LocalForge has no connected
account or API key for them and cannot generate through them yet. Those services
generally charge for API generation; adding one would require a provider
integration and a configured account. No cloud provider is called by this app.

Start ComfyUI separately:

```bash
./scripts/start-comfyui.sh
```

Then start LocalForge. The dashboard health indicators should show both engines.

## Face swap quality

AlphaFace 256 is the installed swapper. It was chosen for source likeness and
pose handling, then verified on a frame using CoreML with two processing
threads. The other tested swapper weights and GFP-GAN enhancement were removed
to save space. LocalForge checks for installed weights before starting a job,
so an unavailable model cannot trigger a large download through the API.
For a clip with one visible person, leave **Face Selection** on **One person**.
Use **Reference match** when a clip has multiple people and the reference face
needs to identify the intended person. Test a short close-up before processing
a full clip.

The output quality control changes encoding, not the detail in the source.
For HD results, start with a high-quality source video. ComfyUI video generation
requires separate model weights and tested workflow files.

FaceFusion uses two processing threads by default so short tests leave more CPU
available for other apps. Video frames are processed through temporary files to
limit RAM use, and FaceFusion releases its model cache after each job. The
temporary files are removed when a job ends.

The dashboard reports face-swap progress by stage: frame extraction, face
processing, and video encoding. Ultra uses 1024px face processing and can take
much longer on a 16GB M1 Pro than the recommended High Profile Studio setting.
ComfyUI jobs show an activity indicator because ComfyUI workflow steps do not
provide a reliable overall percentage.

## Files and cleanup

Completed media is saved directly in `data/outputs/` by default. Set
`LOCALFORGE_OUTPUT_DIR` in `.env` to use another local folder. The dashboard's
**Open output folder** and **Show in Finder** buttons open those existing files;
they do not make a second copy in the browser's Downloads folder.

Before submitting a job, **Remove** clears a selected source image or target
video from the form. After submitting, **Cancel** stops the running job; then
**Delete files** removes the job and its uploaded copies. Long filenames are
shortened visually in the picker; hovering shows the full name.

After a successful job, LocalForge removes its copied input uploads. Failed
jobs keep their inputs so **Retry** works. **Delete files** permanently removes
a job's output, remaining uploads, log, and temporary files. **Clear failed
jobs** removes all failed jobs and their files. **Clear outputs older than 30
days** is available when old completed jobs exist and runs only when clicked.
Completed outputs otherwise stay until you delete them. Temporary files left
by a crash are removed on startup once they are over 24 hours old. The
dashboard shows current output, upload, and temporary storage use.

## ComfyUI workflows

ComfyUI's API format is model-specific. Export each tested workflow using
**Save (API Format)** and put it under `workflows/` using these names:

- `text_to_image.json`
- `image_to_video.json`
- `text_to_video.json`

LocalForge recursively replaces these literal tokens in the workflow JSON:

- `{{PROMPT}}`
- `{{NEGATIVE_PROMPT}}`
- `{{INPUT_IMAGE}}`
- `{{SEED}}`
- `{{WIDTH}}`
- `{{HEIGHT}}`
- `{{FRAMES}}`

An SD 1.5-compatible starter text-to-image workflow is included. Select the
checkpoint filename in `.env`. The existing video workflow JSON files are
not usable: image-to-video produces only one frame, and text-to-video references
Wan weights that are not installed. LocalForge keeps both video tabs disabled.
A working video setup needs a compatible model and a tested multi-frame workflow.

## Storage

```text
data/
  localforge.sqlite3
  uploads/<job-id>/
  outputs/<generated-file>
  logs/<job-id>.log
  tmp/<job-id>/
```

Delete jobs and media from the dashboard when they are no longer needed.

## Security and operational notes

- The service is local-only by default and has no login.
- Uploaded filenames are normalized and each job gets an isolated directory.
- Subprocesses are launched without a shell.
- The worker only invokes configured engine executables and workflow files.
- Keep original media and generated media separated; disclose synthetic media
  when publishing it and use faces you are authorized to use.
- Back up `data/` if job history matters. It is excluded from Git.

## Development

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

API docs are available at <http://127.0.0.1:8787/docs> while running.
