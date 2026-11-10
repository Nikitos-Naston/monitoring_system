CREATE TABLE IF NOT EXISTS profiles (
    name text PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS template_versions (
    profile text NOT NULL REFERENCES profiles(name),
    version integer NOT NULL CHECK (version > 0),
    source text NOT NULL CHECK (octet_length(source) BETWEEN 1 AND 65536),
    active boolean NOT NULL DEFAULT false,
    PRIMARY KEY (profile, version)
);

CREATE UNIQUE INDEX IF NOT EXISTS one_active_version_per_profile
    ON template_versions (profile) WHERE active;

CREATE TABLE IF NOT EXISTS poll_routes (
    device_id text NOT NULL,
    poll_id text NOT NULL,
    asset_id text NOT NULL,
    profile text NOT NULL REFERENCES profiles(name),
    start_address integer NOT NULL CHECK (start_address BETWEEN 0 AND 65535),
    register_count integer NOT NULL CHECK (register_count BETWEEN 1 AND 125),
    word_order text NOT NULL CHECK (word_order IN ('high_first', 'low_first')),
    PRIMARY KEY (device_id, poll_id),
    CHECK (start_address + register_count <= 65536)
);
