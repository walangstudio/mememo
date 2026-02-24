# Contributing to mememo

Thank you for your interest in contributing to mememo! This document provides guidelines and instructions for contributing.

## Development Notes

This project was developed with assistance from [Claude AI](https://claude.ai) for rapid prototyping and architecture design. All code has been thoroughly reviewed, tested, and validated by the maintainers to ensure quality and correctness.

## Development Setup

### Prerequisites
- Python 3.10+
- Git

### Setup Instructions

1. **Fork and clone the repository**
```bash
git clone https://github.com/scr1p7k177y/mememo.git
cd mememo
```

2. **Install development dependencies**
```bash
# Linux/macOS
bash install.sh --dev

# Windows
install.bat --dev
```

3. **Run tests to verify setup**
```bash
pytest tests/ -v
```

## Development Workflow

### Code Style

We use the following tools to maintain code quality:
- **Black** for code formatting
- **Ruff** for linting
- **MyPy** for type checking

Before committing, run:
```bash
# Format code
black mememo tests

# Check linting
ruff check mememo tests

# Type check
mypy mememo --ignore-missing-imports
```

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=mememo --cov-report=html

# Run specific test file
pytest tests/test_integration.py -v
```

### Git Workflow

1. **Create a feature branch**
```bash
git checkout -b feature/your-feature-name
```

2. **Make your changes**
- Write code
- Add tests
- Update documentation

3. **Commit your changes**
```bash
git add .
git commit -m "feat: your feature description"
```

We follow [Conventional Commits](https://www.conventionalcommits.org/):
- `feat:` New features
- `fix:` Bug fixes
- `docs:` Documentation changes
- `refactor:` Code refactoring
- `test:` Test additions/changes
- `chore:` Build/tooling changes

4. **Push and create a pull request**
```bash
git push origin feature/your-feature-name
```

## Pull Request Guidelines

### Before Submitting

- [ ] Tests pass locally (`pytest tests/ -v`)
- [ ] Code is formatted (`black mememo tests`)
- [ ] Linting passes (`ruff check mememo tests`)
- [ ] Type checking passes (`mypy mememo`)
- [ ] Documentation is updated (if needed)
- [ ] CHANGELOG.md is updated (for significant changes)

### PR Description

Please include:
- **What** - What does this PR do?
- **Why** - Why is this change needed?
- **How** - How does it work?
- **Testing** - How did you test it?

## Adding New Languages

To add support for a new programming language:

1. **Add tree-sitter parser** (if not already in `tree-sitter-languages`)
2. **Create chunker** in `mememo/chunking/`
3. **Register language** in `mememo/chunking/factory.py`
4. **Add tests** in `tests/`
5. **Update README** with language support

See existing chunkers (`python_chunker.py`, `treesitter_chunker.py`) for examples.

## Reporting Issues

### Bug Reports

Please include:
- **Description** - What happened?
- **Expected behavior** - What should happen?
- **Steps to reproduce** - How can we reproduce it?
- **Environment** - OS, Python version, mememo version
- **Logs/errors** - Any relevant error messages

### Feature Requests

Please include:
- **Use case** - What problem does this solve?
- **Proposed solution** - How should it work?
- **Alternatives** - What other solutions did you consider?

## Code of Conduct

### Our Pledge

We are committed to providing a welcoming and inclusive environment for all contributors.

### Expected Behavior

- Be respectful and considerate
- Provide constructive feedback
- Focus on what is best for the community
- Show empathy towards others

### Unacceptable Behavior

- Harassment or discriminatory language
- Personal attacks or trolling
- Public or private harassment
- Spam or promotional content

## Questions?

- Open a [GitHub Discussion](https://github.com/scr1p7k177y/mememo/discussions)
- Check existing [Issues](https://github.com/scr1p7k177y/mememo/issues)

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
