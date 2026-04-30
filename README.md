# micronoc

Repositorio base para desarrollar el proyecto `micronoc`.

## Estado Actual
Proyecto Python con FastAPI, pruebas con pytest y una CLI básica.

## Estructura
- `app/`: código de aplicación (API FastAPI + CLI).
- `tests/`: pruebas unitarias/integración.
- `scripts/`: automatizaciones de desarrollo y CI.
- `docs/`: documentación técnica y decisiones de arquitectura.
- `assets/` (opcional): archivos estáticos y fixtures.

## Comandos de Desarrollo

```bash
make setup    # instalar dependencias
make dev      # ejecutar en local
make run      # ejecutar sin reload
make cli ARGS="health"  # ejecutar la CLI
make test     # ejecutar pruebas
make lint     # chequeos estáticos
make format   # formateo de código
```

## CLI

La CLI se ejecuta como módulo Python:

```bash
python -m app.cli health
python -m app.cli serve --host 0.0.0.0 --port 8000 --reload
```

## Convenciones de Contribución
- Sigue las reglas en `AGENTS.md`.
- Usa commits tipo Conventional Commits:
  - `feat: ...`
  - `fix: ...`
  - `docs: ...`
- Incluye pruebas o evidencia de validación en cada PR.

## Configuración y Seguridad
- No subir secretos al repo.
- Usar variables de entorno y mantener un `.env.example` actualizado.
- Documentar variables requeridas y defaults seguros.
