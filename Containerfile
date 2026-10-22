FROM python:3.14-slim@sha256:0514bc76712d64f8bb48adcb95b1ab0d88c69fee95d62fe4c18ee948bee9b1e1

# pip vendors certifi and ignores PIP_CERT. Reject a base image that still
# ships pip older than the emSign CA fix (26.2.2).
# https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576
RUN python -c 'import pip, sys; parts=tuple(int("".join(c for c in p if c.isdigit()) or "0") for p in pip.__version__.split(".")[:3]); sys.exit("pip %s is older than 26.2.2" % pip.__version__) if parts < (26, 2, 2) else print("pip", pip.__version__)'

# Install uv for fast, reproducible dependency resolution.
COPY --from=ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY bot/ bot/
RUN uv sync --frozen --no-dev

RUN useradd --no-create-home --shell /bin/false app \
    && chown -R app:app /app
USER app

CMD ["uv", "run", "python", "-m", "bot.main"]
