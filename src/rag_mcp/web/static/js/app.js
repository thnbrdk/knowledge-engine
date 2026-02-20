/* ═══════════════════════════════════════════════════════════════════════════
   RAG Knowledge Server — JavaScript (admin actions, toast, auth)
   ═══════════════════════════════════════════════════════════════════════════ */

let _adminToken = '';

function getAuthHeaders() {
    const headers = { 'Content-Type': 'application/json' };
    if (_adminToken) {
        headers['Authorization'] = 'Bearer ' + _adminToken;
    }
    return headers;
}

function showToast(message, type) {
    type = type || 'info';
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = message;
    toast.className = 'toast toast--visible toast--' + type;
    setTimeout(function () {
        toast.className = 'toast';
    }, 3000);
}

function setAdminToken() {
    const input = document.getElementById('admin-token-input');
    if (!input) return;
    _adminToken = input.value;
    document.cookie = 'admin_token=' + encodeURIComponent(_adminToken) + '; path=/; SameSite=Strict';
    const gate = document.getElementById('auth-gate');
    const content = document.getElementById('admin-content');
    if (gate) gate.style.display = 'none';
    if (content) content.style.display = 'block';
    loadAdminStats();
    showToast('Authenticated', 'success');
}

function loadAdminStats() {
    fetch('/api/admin/stats', { headers: getAuthHeaders() })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            var el;
            el = document.getElementById('stat-docs');
            if (el) el.textContent = data.total_docs || 0;
            el = document.getElementById('stat-cats');
            if (el) el.textContent = data.total_categories || 0;
            el = document.getElementById('stat-sqlite');
            if (el) el.textContent = (data.sqlite_size_mb || 0) + ' MB';
            el = document.getElementById('stat-vector');
            if (el) el.textContent = (data.vector_size_mb || 0) + ' MB';
        })
        .catch(function () {
            showToast('Failed to load stats', 'error');
        });
}

function reindexAll() {
    if (!confirm('Re-index all categories? This will re-check every file for changes.')) {
        return;
    }
    var btn = document.getElementById('btn-reindex-all');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Re-indexing…';
    }
    fetch('/api/admin/reindex-all', { method: 'POST', headers: getAuthHeaders() })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            showToast('Re-index complete: ' + data.new + ' new, ' + data.updated + ' updated, ' + data.deleted + ' deleted', 'success');
            loadAdminStats();
            setTimeout(function () { location.reload(); }, 1000);
        })
        .catch(function () {
            showToast('Re-index failed', 'error');
        })
        .finally(function () {
            if (btn) {
                btn.disabled = false;
                btn.textContent = 'Re-index All Categories';
            }
        });
}

function reindexCategory(category) {
    if (!confirm('Re-index category "' + category + '"?')) {
        return;
    }
    showToast('Re-indexing ' + category + '…', 'info');
    fetch('/api/admin/reindex/' + encodeURIComponent(category), {
        method: 'POST',
        headers: getAuthHeaders(),
    })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            showToast('Re-indexed ' + category + ': ' + data.indexed + ' files', 'success');
            loadAdminStats();
        })
        .catch(function () {
            showToast('Failed to re-index ' + category, 'error');
        });
}

function deleteCategory(category) {
    if (!confirm('Delete category "' + category + '" from the index?\n\nFiles on disk will NOT be deleted unless you choose to.')) {
        return;
    }
    var deleteFiles = confirm('Also delete files from disk?');
    fetch('/api/admin/category/' + encodeURIComponent(category), {
        method: 'DELETE',
        headers: getAuthHeaders(),
        body: JSON.stringify({ delete_files: deleteFiles }),
    })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            showToast('Deleted category: ' + category, 'success');
            var row = document.querySelector('tr[data-category="' + category + '"]');
            if (row) row.remove();
            loadAdminStats();
        })
        .catch(function () {
            showToast('Failed to delete ' + category, 'error');
        });
}
