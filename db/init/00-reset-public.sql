-- Local and CI only: the web schema snapshot recreates `public` itself.
DROP SCHEMA IF EXISTS public CASCADE;
