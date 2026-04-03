function transcribeApp() {
    return {
        loading: false,
        transcriptions: [],
        cameras: [],
        dates: [],
        stats: { total: 0, completed: 0, processing: 0, errors: 0, today: 0 },
        filters: { search: '', camera: '', date: '', status: '' },
        pagination: { page: 1, per_page: 20, total: 0, pages: 0 },
        confirmModal: { show: false, action: '', title: '', message: '', item: null, loading: false },
        // Settings
        showSettings: false,
        settingsTab: 'transcription',
        settingsLoading: false,
        settingsSaved: false,
        whisperStatus: null,
        protectStatus: null,
        // Speaches model management
        speachesModels: [],
        speachesModelsLoading: false,
        speachesModelsError: null,
        downloadConfirm: { show: false, model: null },
        // Sync modal
        showSyncModal: false,
        syncLoading: false,
        syncHours: '24',
        syncResult: null,
        // Reset modal
        showResetConfirm: false,
        resetConfirmText: '',
        resetLoading: false,
        resetResult: null,
        retryingAllErrors: false,
        retranscribing: false,
        retranscribeResult: null,
        // Summaries modal
        showSummaries: false,
        summaryPeriod: 'daily',
        summaryItems: [],
        summariesLoading: false,
        // Dark mode
        darkMode: document.documentElement.classList.contains('dark'),
        // Toast
        toast: { show: false, message: '', type: 'info' },
        // Audio playback
        activeAudio: { id: null, time: 0 },
        settings: {
            whisper_model: 'Systran/faster-whisper-large-v3',
            language: 'da',
            buffer_before: '5',
            buffer_after: '60',
            vad_filter: 'true',
            beam_size: '5',
            protect_host: '',
            ollama_url: '',
            ollama_model: '',
            condition_on_previous_text: 'false',
            no_speech_threshold: '0.6',
            compression_ratio_threshold: '2.4',
        },
        availableLanguages: [],

        async init() {
            await this.loadAll();
            await this.loadSettings();
            setInterval(() => this.refresh(), 30000);
        },

        async loadAll() {
            await Promise.all([
                this.loadStats(),
                this.loadCameras(),
                this.loadDates(),
                this.loadTranscriptions()
            ]);
        },

        async refresh() { await this.loadAll(); },

        async loadStats() {
            try { this.stats = await (await fetch('/api/stats')).json(); }
            catch (e) { console.error(e); }
        },

        async loadCameras() {
            try { this.cameras = (await (await fetch('/api/cameras')).json()).cameras; }
            catch (e) { console.error(e); }
        },

        async loadDates() {
            try { this.dates = (await (await fetch('/api/dates')).json()).dates; }
            catch (e) { console.error(e); }
        },

        async loadTranscriptions() {
            this.loading = true;
            try {
                const p = new URLSearchParams({ page: this.pagination.page, per_page: this.pagination.per_page });
                if (this.filters.search) p.append('search', this.filters.search);
                if (this.filters.camera) p.append('camera', this.filters.camera);
                if (this.filters.date)   p.append('date', this.filters.date);
                if (this.filters.status) p.append('status', this.filters.status);
                const data = await (await fetch(`/api/transcriptions?${p}`)).json();
                this.transcriptions = data.transcriptions;
                this.pagination.total = data.total;
                this.pagination.pages = data.pages;
            } catch (e) { console.error(e); }
            finally { this.loading = false; }
        },

        get groupedTranscriptions() {
            const groups = {};
            for (const item of this.transcriptions) {
                const date = item.timestamp ? item.timestamp.split('T')[0] : 'unknown';
                if (!groups[date]) groups[date] = [];
                groups[date].push(item);
            }
            return groups;
        },

        formatDate(dateStr) {
            if (!dateStr || dateStr === 'unknown') return 'Unknown date';
            const date = new Date(dateStr);
            const today = new Date();
            const yesterday = new Date(today); yesterday.setDate(yesterday.getDate() - 1);
            if (date.toDateString() === today.toDateString()) return 'Today';
            if (date.toDateString() === yesterday.toDateString()) return 'Yesterday';
            return date.toLocaleDateString('da-DK', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
        },

        formatTime(timestamp) {
            if (!timestamp) return '';
            return new Date(timestamp).toLocaleTimeString('da-DK', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        },

        formatRelativeTime(timestamp) {
            if (!timestamp) return '';
            const diffSec = Math.floor((new Date() - new Date(timestamp)) / 1000);
            if (diffSec < 60) return 'just now';
            if (diffSec < 3600) return `${Math.floor(diffSec/60)}m ago`;
            if (diffSec < 86400) return `${Math.floor(diffSec/3600)}h ago`;
            const days = Math.floor(diffSec/86400);
            if (days < 7) return `${days}d ago`;
            return this.formatTime(timestamp);
        },

        formatFullTimestamp(timestamp) {
            if (!timestamp) return '';
            return new Date(timestamp).toLocaleString('da-DK', {
                weekday: 'short', year: 'numeric', month: 'short',
                day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit'
            });
        },

        formatDuration(seconds) {
            if (!seconds) return '';
            const m = Math.floor(seconds / 60), s = Math.floor(seconds % 60);
            return m > 0 ? `${m}:${s.toString().padStart(2,'0')}` : `${s}s`;
        },

        formatSegmentTime(seconds) {
            if (seconds === undefined || seconds === null) return '0:00';
            const m = Math.floor(seconds / 60), s = Math.floor(seconds % 60);
            return `${m}:${s.toString().padStart(2,'0')}`;
        },

        seekAudio(itemId, seconds) {
            const audio = document.getElementById('audio-' + itemId);
            if (audio) { audio.currentTime = seconds; audio.play(); this.activeAudio = { id: itemId, time: seconds }; }
        },

        updateActiveSegment(itemId, currentTime) {
            this.activeAudio = { id: itemId, time: currentTime };
            const item = this.transcriptions.find(t => t.id === itemId);
            if (item && item.segments) {
                for (let i = 0; i < item.segments.length; i++) {
                    const seg = item.segments[i];
                    if (currentTime >= seg.start && currentTime < (seg.end || seg.start + 5)) {
                        document.getElementById(`seg-${itemId}-${i}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                        break;
                    }
                }
            }
        },

        isSegmentActive(itemId, start, end) {
            if (this.activeAudio.id !== itemId) return false;
            return this.activeAudio.time >= start && this.activeAudio.time < (end || start + 5);
        },

        async copyToClipboard(text) {
            try { await navigator.clipboard.writeText(text); this.showToast('Copied!', 'success'); }
            catch { this.showToast('Copy failed', 'error'); }
        },

        showToast(message, type = 'info') {
            this.toast = { show: true, message, type };
            setTimeout(() => { this.toast.show = false; }, 3000);
        },

        highlightSearch(text) {
            if (!text || !this.filters.search) return text;
            const re = new RegExp(`(${this.filters.search.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')})`, 'gi');
            return text.replace(re, '<mark>$1</mark>');
        },

        setQuickFilter(filter) {
            this.filters.date = '';
            this.filters.status = '';
            const today = new Date().toISOString().split('T')[0];
            if (filter === 'today') this.filters.date = today;
            else if (filter === 'errors') this.filters.status = 'error';
            else if (filter === 'processing') this.filters.status = 'processing';
            this.loadTranscriptions();
        },

        prevPage() { if (this.pagination.page > 1) { this.pagination.page--; this.loadTranscriptions(); } },
        nextPage() { if (this.pagination.page < this.pagination.pages) { this.pagination.page++; this.loadTranscriptions(); } },

        toggleDarkMode() {
            this.darkMode = !this.darkMode;
            document.documentElement.classList.toggle('dark', this.darkMode);
            localStorage.setItem('theme', this.darkMode ? 'dark' : 'light');
        },

        confirmRetry(item) {
            this.confirmModal = { show: true, action: 'retry', title: 'Retry Transcription?',
                message: 'This will re-fetch the audio and create a new transcription.', item, loading: false };
        },

        confirmDelete(item) {
            this.confirmModal = { show: true, action: 'delete', title: 'Delete Transcription?',
                message: 'This will permanently delete the transcription and audio file.', item, loading: false };
        },

        async executeAction() {
            if (!this.confirmModal.item) return;
            this.confirmModal.loading = true;
            const { item, action } = this.confirmModal;
            try {
                if (action === 'delete') {
                    const r = await fetch(`/api/transcriptions/${item.id}`, { method: 'DELETE' });
                    if (!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || `Delete failed (${r.status})`);
                    this.transcriptions = this.transcriptions.filter(t => t.id !== item.id);
                    this.showToast('Deleted', 'success');
                } else if (action === 'retry') {
                    const r = await fetch(`/api/transcriptions/${item.id}/retry`, { method: 'POST' });
                    if (!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || `Retry failed (${r.status})`);
                    this.showToast('Retry queued', 'success');
                }
                this.confirmModal.show = false;
                await this.loadAll();
            } catch (e) { this.showToast(e.message, 'error'); }
            finally { this.confirmModal.loading = false; }
        },

        async retryAllErrors() {
            this.retryingAllErrors = true;
            try {
                const r = await fetch('/api/transcriptions/retry-errors', { method: 'POST' });
                if (!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || `Failed (${r.status})`);
                this.showToast((await r.json()).message, 'success');
                await this.loadAll();
            } catch (e) { this.showToast(e.message, 'error'); }
            finally { this.retryingAllErrors = false; }
        },

        async retranscribeAll(includeErrors) {
            this.retranscribing = true; this.retranscribeResult = null;
            try {
                const r = await fetch('/api/transcriptions/retranscribe-all', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ include_errors: includeErrors })
                });
                if (!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || `HTTP ${r.status}`);
                const data = await r.json();
                this.retranscribeResult = data.message;
                this.showToast(data.message, 'success');
                await this.loadAll();
            } catch (e) { this.showToast('Failed: ' + e.message, 'error'); }
            finally { this.retranscribing = false; }
        },

        openSummaries() { this.showSummaries = true; this.loadSummaries(); },

        async loadSummaries() {
            this.summariesLoading = true; this.summaryItems = [];
            try {
                const data = await (await fetch(`/api/summaries?period=${this.summaryPeriod}`)).json();
                this.summaryItems = data.items.map(i => ({ ...i, generating: false }));
            } catch (e) { this.showToast('Failed to load summaries: ' + e.message, 'error'); }
            finally { this.summariesLoading = false; }
        },

        async generateSummary(item) {
            item.generating = true;
            try {
                const r = await fetch('/api/summaries/generate', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ period_type: this.summaryPeriod, period_key: item.period_key })
                });
                if (!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || `HTTP ${r.status}`);
                const data = await r.json();
                item.summary = data.summary;
                item.generated_at = new Date().toISOString().replace('T',' ').slice(0,19);
                item.stale = false;
                this.showToast('Summary generated', 'success');
            } catch (e) { this.showToast('Failed: ' + e.message, 'error'); }
            finally { item.generating = false; }
        },

        async loadSettings() {
            try {
                const data = await (await fetch('/api/settings')).json();
                this.settings = data.settings;
                this.availableLanguages = data.available_languages;
            } catch (e) { console.error(e); }
        },

        openSettings() {
            this.showSettings = true;
            if (this.speachesModels.length === 0) this.loadSpeachesModels();
        },

        async loadSpeachesModels() {
            this.speachesModelsLoading = true;
            this.speachesModelsError = null;
            try {
                const data = await (await fetch('/api/settings/speaches-models')).json();
                this.speachesModels = data.models;
            } catch (e) {
                this.speachesModelsError = 'Could not reach speaches server';
            } finally {
                this.speachesModelsLoading = false;
            }
        },

        confirmDownloadModel(model) {
            this.downloadConfirm = { show: true, model };
        },

        async downloadModel() {
            const model = this.downloadConfirm.model;
            if (!model) return;
            this.downloadConfirm = { show: false, model: null };

            const m = this.speachesModels.find(x => x.id === model.id);
            if (m) m.downloading = true;
            try {
                const r = await fetch(`/api/settings/speaches-models/${model.id}`, { method: 'POST' });
                if (r.ok) {
                    if (m) { m.installed = true; m.downloading = false; }
                    this.settings.whisper_model = model.id;
                    this.showToast(`Model downloaded: ${model.id}`, 'success');
                } else {
                    const err = await r.json();
                    this.showToast(`Download failed: ${err.detail || r.status}`, 'error');
                    if (m) m.downloading = false;
                }
            } catch (e) {
                this.showToast(`Download failed: ${e.message}`, 'error');
                if (m) m.downloading = false;
            }
        },

        async saveSettings() {
            this.settingsLoading = true; this.settingsSaved = false;
            try {
                const r = await fetch('/api/settings', {
                    method: 'PUT', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.settings)
                });
                if (!r.ok) throw new Error((await r.json()).detail || 'Failed to save');
                this.settings = (await r.json()).settings;
                this.settingsSaved = true;
                this.showToast('Settings saved', 'success');
                setTimeout(() => { this.settingsSaved = false; }, 3000);
            } catch (e) { this.showToast('Save failed: ' + e.message, 'error'); }
            finally { this.settingsLoading = false; }
        },

        async testWhisper() {
            this.whisperStatus = { loading: true };
            try { this.whisperStatus = await (await fetch('/api/settings/test-whisper', { method: 'POST' })).json(); }
            catch (e) { this.whisperStatus = { status: 'error', message: e.message }; }
        },

        async testProtect() {
            this.protectStatus = { loading: true };
            try {
                await this.saveSettings();
                this.protectStatus = await (await fetch('/api/settings/test-protect', { method: 'POST' })).json();
            } catch (e) { this.protectStatus = { status: 'error', message: e.message }; }
        },

        async runSync() {
            this.syncLoading = true; this.syncResult = null;
            try {
                const r = await fetch(`/api/sync?hours=${this.syncHours}`, { method: 'POST' });
                const data = await r.json();
                this.syncResult = r.ok ? data : { status: 'error', message: data.detail || 'Sync failed' };
                if (r.ok && data.events_queued > 0) {
                    this.showToast(`Queued ${data.events_queued} events`, 'success');
                    setTimeout(() => this.loadAll(), 2000);
                }
            } catch (e) { this.syncResult = { status: 'error', message: e.message }; }
            finally { this.syncLoading = false; }
        },

        async performReset() {
            if (this.resetConfirmText !== 'RESET') return;
            this.resetLoading = true; this.resetResult = null;
            try {
                const r = await fetch('/api/database/reset', { method: 'POST' });
                const data = await r.json();
                this.resetResult = data;
                if (r.ok) {
                    this.showToast('Database reset', 'success');
                    setTimeout(() => {
                        this.showResetConfirm = false; this.showSettings = false;
                        this.resetConfirmText = ''; this.resetResult = null;
                        this.loadAll();
                    }, 2000);
                }
            } catch (e) { this.resetResult = { status: 'error', message: e.message }; }
            finally { this.resetLoading = false; }
        }
    }
}
