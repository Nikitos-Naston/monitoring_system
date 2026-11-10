"""One repository contract for fixture and PostgreSQL modes."""
from pathlib import Path

from .fixtures import ROUTES, TEMPLATES, Route, Template


class TemplateUnavailable(ValueError):
    pass


class FixtureTemplates:
    def __init__(self):
        self.routes = {(r.device_id, r.poll_id): r for r in ROUTES}
        self.templates = {t.profile: t for t in TEMPLATES}

    def resolve(self, device_id, poll_id):
        route = self.routes.get((device_id, poll_id))
        if route is None:
            raise TemplateUnavailable("Unknown device and poll")
        return route, self.templates[route.profile]


class PostgresTemplates:
    def __init__(self, dsn):
        if not dsn:
            raise ValueError("DATABASE_URL is required for postgres mode")
        self.dsn = dsn

    def resolve(self, device_id, poll_id):
        import psycopg

        # A single SQL statement observes the route and active version together.
        with psycopg.connect(
            self.dsn, connect_timeout=3, options="-c statement_timeout=3000"
        ) as connection:
            row = connection.execute("""
                SELECT r.device_id, r.poll_id, r.asset_id, r.profile,
                       r.start_address, r.register_count, r.word_order, v.version, v.source
                FROM poll_routes r
                LEFT JOIN template_versions v ON v.profile = r.profile AND v.active
                WHERE r.device_id = %s AND r.poll_id = %s
            """, (device_id, poll_id)).fetchone()
        if row is None:
            raise TemplateUnavailable("Unknown device and poll")
        if row[7] is None:
            raise TemplateUnavailable("No active template for poll")
        return Route(*row[:7]), Template(row[3], row[7], row[8])


def seed_demo(dsn):
    import psycopg

    if not dsn:
        raise ValueError("DATABASE_URL is required")
    with psycopg.connect(dsn, connect_timeout=3) as connection:
        connection.execute(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
        for template in TEMPLATES:
            connection.execute("INSERT INTO profiles VALUES (%s) ON CONFLICT DO NOTHING",
                               (template.profile,))
            connection.execute("""
                INSERT INTO template_versions (profile, version, source, active)
                SELECT %s, %s, %s, NOT EXISTS (
                    SELECT 1 FROM template_versions WHERE profile = %s AND active
                )
                ON CONFLICT (profile, version) DO NOTHING
            """, (template.profile, template.version, template.source, template.profile))
        for r in ROUTES:
            connection.execute("""
                INSERT INTO poll_routes VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (device_id, poll_id) DO NOTHING
            """, (r.device_id, r.poll_id, r.asset_id, r.profile, r.start, r.count, r.word_order))
