import nox


@nox.session(python=["3.11", "3.12", "3.13"])
def test(session):
    session.install(".[dev]")
    session.run("pytest", *session.posargs)


@nox.session
def lint(session):
    session.install("ruff")
    session.run("ruff", "check", "src/", "tests/", "scripts/")


@nox.session
def mypy(session):
    session.install(".[dev]", "mypy")
    session.run("mypy", "src/", "--ignore-missing-imports")


@nox.session
def coverage(session):
    session.install(".[dev]", "pytest-cov")
    session.run(
        "pytest",
        "--cov=boundary_analyzer",
        "--cov-report=term-missing",
        "--cov-report=xml",
        *session.posargs,
    )


@nox.session
def ci(session):
    lint(session)
    mypy(session)
    coverage(session)
