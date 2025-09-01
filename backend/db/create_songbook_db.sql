-- USERS
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL
);

-- AUTHORS
CREATE TABLE IF NOT EXISTS authors (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

-- SONGS
CREATE TABLE IF NOT EXISTS songs (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    author_id INTEGER,
    FOREIGN KEY (author_id) REFERENCES authors(id)
);

-- SONG IMAGES
CREATE TABLE IF NOT EXISTS song_images (
    id INTEGER PRIMARY KEY,
    song_id TEXT NOT NULL,
    image_path TEXT NOT NULL,
    FOREIGN KEY (song_id) REFERENCES songs(id)
);

-- SONGBOOKS
CREATE TABLE IF NOT EXISTS songbooks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    owner_id INTEGER,
    first_page_side TEXT DEFAULT 'right',  -- např. "left" nebo "right"
    img_path_cover_preview TEXT,
    img_path_cover_front_outer TEXT,
    img_path_cover_front_inner TEXT,
    img_path_cover_back_inner TEXT,
    img_path_cover_back_outer TEXT,
    is_public INTEGER DEFAULT 0,
    FOREIGN KEY (owner_id) REFERENCES users(id)
);

-- SONGBOOK INTRO/OUTRO IMAGES
CREATE TABLE IF NOT EXISTS songbook_intro_outro_images (
    id INTEGER PRIMARY KEY,
    songbook_id TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('intro', 'outro')),
    image_path TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0,
    FOREIGN KEY (songbook_id) REFERENCES songbooks(id)
);

-- USER → SONGBOOK ACCESS
CREATE TABLE IF NOT EXISTS user_songbook_access (
    user_id INTEGER NOT NULL,
    songbook_id TEXT NOT NULL,
    PRIMARY KEY (user_id, songbook_id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (songbook_id) REFERENCES songbooks(id)
);

-- SONGBOOK PAGES
CREATE TABLE IF NOT EXISTS songbook_pages (
    songbook_id TEXT NOT NULL,
    song_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    PRIMARY KEY (songbook_id, page_number),
    FOREIGN KEY (song_id) REFERENCES songs(id),
    FOREIGN KEY (songbook_id) REFERENCES songbooks(id)
);

-- SONG PARTS
CREATE TABLE IF NOT EXISTS song_parts (
    id INTEGER PRIMARY KEY,
    song_id TEXT NOT NULL,
    image_path TEXT NOT NULL,
    position INTEGER DEFAULT 0,
    page_number_hint INTEGER,
    FOREIGN KEY (song_id) REFERENCES songs(id)
);
