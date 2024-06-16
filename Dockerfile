FROM ghcr.io/ad-sdl/wei

LABEL org.opencontainers.image.source=https://github.com/openuc2/ot2_module
LABEL org.opencontainers.image.description="Drivers and REST API's for the Uc2 microscope"
LABEL org.opencontainers.image.licenses=MIT

#########################################
# Module specific logic goes below here #
#########################################

RUN mkdir -p ot2_module

COPY ./src uc2_module/src
COPY ./README.md uc2_module/README.md
COPY ./pyproject.toml uc2_module/pyproject.toml
COPY ./tests uc2_module/tests

RUN --mount=type=cache,target=/root/.cache \
    pip install -e ./uc2_module

CMD ["python", "uc2_module/src/uc2_rest_node.py"]

#########################################
