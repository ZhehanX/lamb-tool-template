# The workshop wizard (frontend/, SvelteKit + adapter-static) is built in its
# own stage and served by FastAPI under /workshop (WORKSHOP_SPA_DIR).
FROM node:24-slim AS spa

WORKDIR /spa

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

WORKDIR /srv/tool

COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt

# tiktoken downloads its o200k_base file on first use. Bake it into the image
# so token estimates do not depend on network access at runtime (without it
# the tool falls back to a cruder estimate, see app/workshop/tokens.py).
ENV TIKTOKEN_CACHE_DIR=/srv/tool/.tiktoken
RUN python -c "import tiktoken; tiktoken.get_encoding('o200k_base')"

COPY app ./app
COPY --from=spa /spa/build ./frontend/build

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
