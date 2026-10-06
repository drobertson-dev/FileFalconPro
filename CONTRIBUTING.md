# Contributing to File Falcon Pro

Thanks for helping out! Bug reports, ideas and pull requests are all welcome.

## Reporting a bug

[Open an issue](https://github.com/drobertson-dev/FileFalconPro/issues/new) and include:

- what you did, what you expected, and what happened instead;
- your macOS version and how you run the app (built `.app` or from source);
- the relevant lines from the log, found in
  `~/Library/Application Support/File Falcon Pro/filefalcon.log`.

For duplicate-detection problems, a couple of example images (or a description of how they
differ) help a lot.

## Making a change

1. Fork the repository and create a branch from `master`.
2. Set up the project — see [docs/development.md](docs/development.md). In short:
   `uv sync`, then `make run`.
3. Make your change. Logic belongs in `operations/` (no Qt imports there) and should come
   with tests in `tests/`; interface code lives in `gui/`.
4. Run `make format` and `make test`.
5. Update the docs in `docs/` and `CHANGELOG.md` if behaviour changes.
6. Open a pull request describing what changed and why.

## Style

The code is formatted with ruff using tabs and a 100-character line length. Prefer small,
well-named functions, keep slow work off the UI thread (use `gui.workers.Task`), and never
let a code path overwrite or delete a user's file without a way back.

## License

By contributing, you agree that your contributions are licensed under the project's
[MIT License](LICENSE).
