# Contributing

## Development Setup

1. Clone the repository
2. Use Python `3.9+`
3. Run the test suite before opening a change:

```bash
python3 -m unittest discover -s tests -v
```

## Guidelines

- Keep the tool dependency-light
- Prefer support for official Instagram JSON exports over unofficial scraping
- Add tests for each new export layout or CLI feature
- Update `README.md` when behavior or usage changes

## Pull Requests

- Keep changes focused
- Include tests for parser or CLI changes
- Mention any export layout assumptions clearly
