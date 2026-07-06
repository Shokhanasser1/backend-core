-- Провижининг ролей БД для dev-стека (схема §3.1, §3.6).
-- Выполняется автоматически postgres-образом при первом старте контейнера
-- (каталог /docker-entrypoint-initdb.d) от имени суперпользователя.
-- Пароли здесь — dev-константы (совпадают с именами ролей); в проде роли
-- создаёт provisioning-документ с реальными кредами.
-- ВНИМАНИЕ: этот файл — единственный источник ролей для dev; он обязан
-- совпадать с shared/db_provisioning.py (там же — рендер для тестов).

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_migrator') THEN
    CREATE ROLE app_migrator LOGIN PASSWORD 'app_migrator' NOSUPERUSER NOBYPASSRLS NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_user') THEN
    CREATE ROLE app_user LOGIN PASSWORD 'app_user' NOSUPERUSER NOBYPASSRLS NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_maintenance') THEN
    CREATE ROLE app_maintenance LOGIN PASSWORD 'app_maintenance' NOSUPERUSER NOBYPASSRLS NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_retention') THEN
    CREATE ROLE app_retention LOGIN PASSWORD 'app_retention' NOSUPERUSER NOBYPASSRLS NOINHERIT;
  END IF;
END
$$;

GRANT CREATE, USAGE ON SCHEMA public TO app_migrator;
GRANT USAGE ON SCHEMA public TO app_user, app_maintenance, app_retention;
