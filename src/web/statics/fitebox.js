/**
 * FITEBOX Web Client v1.0
 * HMAC-SHA256 authentication, WebSocket status, UI controller
 */

const FITEBOX = (() => {
    // === STATE ===
    let _key = null;
    let _ws = null;
    let _wsReconnectTimer = null;
    let _statusData = {};
    let _statusListeners = [];
    let _metricsHistoryListeners = [];
    let _recTimerInterval = null;

    // === CRYPTO: HMAC-SHA256 ===

    async function hmacSign(body) {
        const timestamp = Math.floor(Date.now() / 1000).toString();
        const payload = `${timestamp}:${body}`;
        const key = await crypto.subtle.importKey(
            'raw',
            new TextEncoder().encode(_key),
            { name: 'HMAC', hash: 'SHA-256' },
            false,
            ['sign']
        );
        const sig = await crypto.subtle.sign(
            'HMAC',
            key,
            new TextEncoder().encode(payload)
        );
        const hex = Array.from(new Uint8Array(sig))
            .map(b => b.toString(16).padStart(2, '0'))
            .join('');
        return { signature: hex, timestamp };
    }

    // === API CALLS ===

    async function api(method, path, data = null) {
        if (!_key) throw new Error('Not authenticated');

        const body = data ? JSON.stringify(data) : '';
        const { signature, timestamp } = await hmacSign(body);

        const opts = {
            method,
            headers: {
                'Content-Type': 'application/json',
                'X-Signature': signature,
                'X-Timestamp': timestamp,
            },
        };
        if (body && method !== 'GET') opts.body = body;

        let res;
        try {
            res = await fetch(path, opts);
        } catch (e) {
            toast('Connection lost - cannot reach server', 'error');
            throw e;
        }
        if (res.status === 401 || res.status === 403) {
            logout();
            throw new Error('Authentication failed');
        }
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: 'Request failed' }));
            toast(err.detail || err.message || `Error ${res.status}`, 'error');
        }
        return res.json();
    }

    const get = (path) => api('GET', path);
    const post = (path, data) => api('POST', path, data || {});

    // === AUTH ===

    function getKey() {
        return _key || sessionStorage.getItem('fitebox_key');
    }

    function setKey(key) {
        _key = key;
        sessionStorage.setItem('fitebox_key', key);
    }

    function isAuthenticated() {
        _key = _key || sessionStorage.getItem('fitebox_key');
        return !!_key;
    }

    function logout() {
        _key = null;
        sessionStorage.removeItem('fitebox_key');
        if (_ws) _ws.close();
        window.location.href = '/';
    }

    async function authenticate(key) {
        const res = await fetch('/api/auth/check', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key }),
        });
        if (res.ok) {
            setKey(key);
            return true;
        }
        return false;
    }

    // === WEBSOCKET ===

    function connectWS() {
        if (_ws && _ws.readyState <= 1) return;
        if (!_key) return;

        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        _ws = new WebSocket(`${proto}//${location.host}/ws`);

        _ws.onopen = () => {
            console.log('[WS] Connected');
            // Authenticate
            _ws.send(JSON.stringify({ key: _key }));
        };

        _ws.onmessage = (e) => {
            try {
                const msg = JSON.parse(e.data);
                if (msg.type === 'auth' && msg.status === 'ok') {
                    console.log('[WS] Authenticated');
                    updateConnIndicator(true);
                } else if (msg.type === 'status_update' || msg.type === 'event') {
                    const data = msg.data || {};
                    Object.assign(_statusData, data);
                    _statusListeners.forEach(fn => fn(_statusData));
                } else if (msg.type === 'metrics_history') {
                    _metricsHistoryListeners.forEach(fn => fn(msg));
                } else if (msg.type === 'error') {
                    console.error('[WS] Error:', msg);
                    if (msg.message === 'Invalid key') {
                        logout();
                    }
                }
            } catch (err) {
                console.error('[WS] Parse error:', err);
            }
        };

        _ws.onclose = () => {
            console.log('[WS] Disconnected');
            updateConnIndicator(false);
            // Reconnect after 3s
            clearTimeout(_wsReconnectTimer);
            _wsReconnectTimer = setTimeout(connectWS, 3000);
        };

        _ws.onerror = () => {
            _ws.close();
        };
    }

    function updateConnIndicator(connected) {
        document.querySelectorAll('.conn-dot').forEach(el => {
            el.classList.toggle('connected', connected);
            el.classList.toggle('disconnected', !connected);
        });
        const banner = document.getElementById('conn-banner');
        if (banner) {
            banner.classList.toggle('hidden', connected);
        }
    }

    // === STATUS LISTENERS ===

    function onStatus(fn) {
        _statusListeners.push(fn);
        // Immediate call with current data
        if (Object.keys(_statusData).length > 0) fn(_statusData);
    }

    function onMetricsHistory(fn) {
        _metricsHistoryListeners.push(fn);
    }

    function getStatus() { return _statusData; }

    // === TOAST NOTIFICATIONS ===

    function toast(message, type = 'info') {
        let container = document.getElementById('toast-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'toast-container';
            container.className = 'toast-container';
            document.body.appendChild(container);
        }

        const el = document.createElement('div');
        el.className = `toast ${type}`;
        el.textContent = message;
        container.appendChild(el);

        setTimeout(() => {
            el.style.opacity = '0';
            setTimeout(() => el.remove(), 200);
        }, 3000);
    }

    // === CONFIRM DIALOG ===

    function confirm(title, text) {
        return new Promise(resolve => {
            const overlay = document.createElement('div');
            overlay.className = 'dialog-overlay';
            overlay.innerHTML = `
                <div class="dialog-box">
                    <div class="dialog-title">${title}</div>
                    <div class="dialog-text">${text}</div>
                    <div class="dialog-actions">
                        <button class="btn" id="dlg-cancel">Cancel</button>
                        <button class="btn btn-danger" id="dlg-confirm">Confirm</button>
                    </div>
                </div>`;
            document.body.appendChild(overlay);

            overlay.querySelector('#dlg-cancel').onclick = () => {
                overlay.remove();
                resolve(false);
            };
            overlay.querySelector('#dlg-confirm').onclick = () => {
                overlay.remove();
                resolve(true);
            };
            overlay.onclick = (e) => {
                if (e.target === overlay) { overlay.remove(); resolve(false); }
            };
        });
    }

    // === HELPERS ===

    function formatTime(seconds) {
        if (!seconds || seconds < 0) return '00:00:00';
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = Math.floor(seconds % 60);
        return [h, m, s].map(v => String(v).padStart(2, '0')).join(':');
    }

    function formatSize(mb) {
        if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
        return `${mb.toFixed(0)} MB`;
    }

    function formatDate(ts) {
        if (!ts) return '-';
        const d = new Date(ts * 1000);
        return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    // === RECORDING TIMER ===

    function startRecTimer() {
        stopRecTimer();
        _recTimerInterval = setInterval(() => {
            if (_statusData.recording) {
                _statusData.recording_time = (_statusData.recording_time || 0) + 1;
                _statusListeners.forEach(fn => fn(_statusData));
            }
        }, 1000);
    }

    function stopRecTimer() {
        if (_recTimerInterval) {
            clearInterval(_recTimerInterval);
            _recTimerInterval = null;
        }
    }

    // === INIT ===

    function init() {
        _key = getKey();
        if (_key) {
            connectWS();
            startRecTimer();
        }
    }

    // Auto-init on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // === PUBLIC API ===
    return {
        api, get, post,
        authenticate, isAuthenticated, logout,
        onStatus, onMetricsHistory, getStatus,
        toast, confirm,
        formatTime, formatSize, formatDate,
        connectWS,
    };
})();
