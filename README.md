# Tu Web MX CRM

CRM interno para Tu Web MX donde setters prospectan negocios desde un mapa y closers gestionan oportunidades, seguimientos y borradores comerciales.

## Arquitectura

- **Backend:** FastAPI + SQLAlchemy 2 + PostgreSQL 16 (PostGIS + pgvector) + Redis/RQ
- **Frontend:** React 18 + TypeScript + Vite + TanStack Query + MapLibre GL JS
- **Infraestructura:** Docker Compose para desarrollo local

## Estructura del proyecto

```
tuwebmx-crm/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── db/
│   │   ├── modules/
│   │   ├── integrations/
│   │   └── workers/
│   ├── migrations/
│   └── tests/
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   ├── features/
│   │   ├── components/
│   │   └── lib/
│   └── tests/
├── docker-compose.yml
└── README.md
```

## Requisitos

- Docker y Docker Compose
- Node.js 20+ (para desarrollo frontend fuera de Docker)
- Python 3.12+ (para desarrollo backend fuera de Docker)

## Inicio rápido con Docker

```bash
# Clonar el repositorio
cd tuwebmx-crm

# Copiar variables de entorno
cp .env.example .env

# Levantar servicios
docker compose up -d --build

# Ver logs
docker compose logs -f api
```

Servicios disponibles:
- **API:** http://localhost:8001
- **API Docs (Swagger):** http://localhost:8001/api/v1/docs
- **Frontend:** http://localhost:5173
- **MinIO Console:** http://localhost:9001 (minioadmin/minioadmin)
- **PostgreSQL:** localhost:5434 (postgres/postgres)
- **Redis:** localhost:6380

## Desarrollo local (sin Docker)

### Backend

```bash
cd backend

# Crear entorno virtual
python -m venv .venv
source .venv/bin/activate

# Instalar dependencias
pip install -e ".[dev]"

# Configurar variables de entorno
cp ../.env.example .env
# Editar .env con tus valores

# Ejecutar migraciones
alembic upgrade head

# Iniciar servidor
uvicorn app.main:app --reload

# En otra terminal, iniciar worker RQ
rq worker default
```

### Frontend

```bash
cd frontend

# Instalar dependencias
npm install

# Iniciar servidor de desarrollo
npm run dev
```

## Tests

### Backend

```bash
cd backend
pytest -v
ruff check .
mypy app
```

### Frontend

```bash
cd frontend
npm run test
npm run test:e2e
npm run build
```

## Variables de entorno principales

| Variable | Descripción | Requerida |
|----------|-------------|-----------|
| `DATABASE_URL` | URL de conexión a PostgreSQL | Sí |
| `REDIS_URL` | URL de conexión a Redis | Sí |
| `SECRET_KEY` | Clave secreta para JWT (cambiar en producción) | Sí |
| `GOOGLE_PLACES_API_KEY` | API Key de Google Places | Para prospección real |
| `GOOGLE_GEOCODING_API_KEY` | API Key de Google Geocoding | Para prospección real |
| `OPENAI_API_KEY` | API Key de OpenAI | Para funcionalidades IA |
| `LLM_API_URL` / `LLM_API_KEY` / `LLM_MODEL` | Endpoint OpenAI-compatible, útil también para Grok | Para borradores y análisis |
| `TRANSCRIPTION_API_URL` / `TRANSCRIPTION_API_KEY` | Endpoint compatible con Whisper | Para transcripción automática |
| `STORAGE_*` | Configuración de almacenamiento S3/MinIO | Sí |

## Migraciones de base de datos

```bash
cd backend

# Crear nueva migración
alembic revision --autogenerate -m "descripcion"

# Aplicar migraciones
alembic upgrade head

# Ver estado actual
alembic current

# Revertir última migración
alembic downgrade -1
```

## Documentación

- **Especificación de diseño:** `docs/superpowers/specs/2026-09-12-tuwebmx-crm-design.md`
- **Plan de implementación:** `docs/superpowers/plans/2026-09-12-tuwebmx-crm-implementation.md`

## Licencia

Proyecto interno de Tu Web MX.
