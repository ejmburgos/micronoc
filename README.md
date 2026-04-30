# micronoc

Proyecto Python con FastAPI, pytest y una CLI basica para monitoreo.

## Estructura
- `app/`: API, CLI, scheduler y servicios.
- `tests/`: pruebas unitarias e integracion.
- `scripts/`: automatizaciones para desarrollo local.
- `.github/`: CI.

## Desarrollo en Windows

Este entorno no trae `make`, asi que el flujo recomendado es PowerShell:

```powershell
.\scripts\tasks.ps1 setup
.\scripts\tasks.ps1 dev
.\scripts\tasks.ps1 run
.\scripts\tasks.ps1 test
.\scripts\tasks.ps1 test -TestArgs "tests\test_cli.py tests\test_health.py"
.\scripts\tasks.ps1 lint
.\scripts\tasks.ps1 format
.\scripts\tasks.ps1 cli -CliArgs "health"
```

## Desarrollo en Unix-like

Si tenes `make`, siguen disponibles estos comandos:

```bash
make setup
make dev
make run
make cli ARGS="health"
make test
make lint
make format
```

## CLI

La CLI tambien puede ejecutarse directo con Python:

```powershell
.\.venv\Scripts\python.exe -m app.cli health
.\.venv\Scripts\python.exe -m app.cli serve --host 0.0.0.0 --port 8000 --reload
```

## Configuracion

- Copiar `.env.example` a `.env` y completar credenciales.
- No subir secretos al repositorio.
- Mantener `.env.example` actualizado cuando cambien variables requeridas.

## Contribucion

- Seguir las reglas de `AGENTS.md`.
- Usar commits tipo Conventional Commits.
- Incluir pruebas o evidencia de validacion en cada PR.
