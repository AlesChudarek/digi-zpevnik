# Repository Reorganization Plan

## Overview
Reorganize the digital songbook project to separate frontend and backend, and add support for private songbooks data storage.

## Steps to Complete

### 1. Create New Directory Structure
- [ ] Create `data/` folder at root
- [ ] Create `data/public/` and `data/private/`
- [ ] Create `data/private/users/` for user-specific private data
- [ ] Create `backend/scripts/` for database scripts

### 2. Move Frontend Assets
- [ ] Move `backend/templates/` → `frontend/templates/`
- [ ] Move `backend/static/` → `frontend/static/`

### 3. Move Public Data
- [ ] Move `backend/db/public_seed/` → `data/public/seeds/`
- [ ] Move `backend/static/songbooks/` → `data/public/images/`

### 4. Move Database Scripts
- [ ] Move DB scripts from `backend/db/` to `backend/scripts/`
- [ ] Update script paths in any references

### 5. Update Flask Configuration
- [ ] Update `app.py` template_folder and static_folder paths
- [ ] Update any hardcoded paths in models/seeds

### 6. Update Path References
- [ ] Update image paths in database seeds
- [ ] Update static file serving in routes
- [ ] Ensure uploads folder is accessible

### 7. Test Application
- [ ] Run the Flask app to verify functionality
- [ ] Check template rendering
- [ ] Check static file serving
- [ ] Test songbook viewing

### 8. Prepare for Private Songbooks
- [ ] Update models if needed for new path structure
- [ ] Plan user folder creation logic for private songbooks
