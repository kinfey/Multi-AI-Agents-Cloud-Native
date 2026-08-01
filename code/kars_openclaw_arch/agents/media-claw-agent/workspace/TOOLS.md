# Tool Conventions

- Use KARS `foundry_web_search` or governed `http_fetch` for current sources.
- Use KARS Foundry tools through the inference router for model operations.
- Use the local `media-claw` command only to execute the deterministic asset, speech, FFmpeg, and Blob publishing pipeline.
- Write temporary artifacts only under `/sandbox` or `/tmp`.
- Do not call public endpoints directly when a governed KARS tool exists.