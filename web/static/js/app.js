/**
 * Upstox 5M F&O Scanner Dashboard - Real-time WebSocket Client
 */

// Application State
let marketDataMap = new Map(); // symbol -> row data
let signalsList = [];
let currentTab = 'signals';
let isAudioEnabled = true;
let searchQuery = '';
let signalFilterDirection = 'ALL';
let signalFilterPattern = 'ALL';

// WebSocket & Polling Instances
let socket = null;
let reconnectTimer = null;
let pollInterval = null;

// Initialize on DOM Load
document.addEventListener('DOMContentLoaded', () => {
    initClock();
    initAudioToggle();
    fetchInitialSnapshot();
    connectWebSocket();
    // Fast polling fallback every 2.5s until WS connects
    pollInterval = setInterval(fetchInitialSnapshot, 2500);
});

async function fetchInitialSnapshot() {
    try {
        const res = await fetch('/api/snapshot');
        if (res.ok) {
            const data = await res.json();
            renderInitialSnapshot(data);
            if (data.market && data.market.length > 0) {
                const badge = document.getElementById('connectionText');
                if (badge && badge.innerText === 'CONNECTING...') {
                    updateConnectionStatus('LIVE CONNECTED', '#10b981');
                }
            }
        }
    } catch (err) {
        console.warn('Failed to fetch snapshot:', err);
    }
}

// ==========================================================================
// 1. Clock & Audio
// ==========================================================================
function initClock() {
    function updateClock() {
        const now = new Date();
        const istOffset = 5.5 * 60 * 60 * 1000;
        const istTime = new Date(now.getTime() + istOffset + (now.getTimezoneOffset() * 60 * 1000));
        const timeStr = istTime.toTimeString().split(' ')[0];
        const clockEl = document.getElementById('liveClock');
        if (clockEl) clockEl.innerText = timeStr;
    }
    updateClock();
    setInterval(updateClock, 1000);
}

function initAudioToggle() {
    const btn = document.getElementById('btnAudioToggle');
    if (!btn) return;
    btn.addEventListener('click', () => {
        isAudioEnabled = !isAudioEnabled;
        btn.innerHTML = isAudioEnabled ? '<span>🔔</span> Sound: ON' : '<span>🔕</span> Sound: OFF';
        btn.style.opacity = isAudioEnabled ? '1' : '0.6';
    });
}

function playSignalChime(isBullish = true) {
    if (!isAudioEnabled) return;
    try {
        const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.connect(gain);
        gain.connect(audioCtx.destination);

        if (isBullish) {
            osc.frequency.setValueAtTime(587.33, audioCtx.currentTime); // D5
            osc.frequency.exponentialRampToValueAtTime(880.00, audioCtx.currentTime + 0.15); // A5
        } else {
            osc.frequency.setValueAtTime(440.00, audioCtx.currentTime); // A4
            osc.frequency.exponentialRampToValueAtTime(293.66, audioCtx.currentTime + 0.2); // D4
        }

        gain.gain.setValueAtTime(0.15, audioCtx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.3);

        osc.start();
        osc.stop(audioCtx.currentTime + 0.35);
    } catch (e) {
        // AudioContext not allowed before gesture or not supported
    }
}

// ==========================================================================
// 2. WebSocket Connection & Message Handling
// ==========================================================================
function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    updateConnectionStatus('CONNECTING...', '#f59e0b');

    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
        updateConnectionStatus('LIVE CONNECTED', '#10b981');
        if (reconnectTimer) {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }
        if (pollInterval) clearInterval(pollInterval);
        pollInterval = setInterval(fetchInitialSnapshot, 10000);
    };

    socket.onmessage = (event) => {
        try {
            const payload = JSON.parse(event.data);
            handleWsMessage(payload);
        } catch (e) {
            console.error('Error parsing WS message:', e);
        }
    };

    socket.onclose = () => {
        updateConnectionStatus('RECONNECTING...', '#f43f5e');
        if (pollInterval) clearInterval(pollInterval);
        pollInterval = setInterval(fetchInitialSnapshot, 2500);
        if (!reconnectTimer) {
            reconnectTimer = setTimeout(connectWebSocket, 2000);
        }
    };

    socket.onerror = (err) => {
        console.warn('WebSocket error:', err);
    };
}

function updateConnectionStatus(text, color) {
    const label = document.getElementById('connectionText');
    const badge = document.getElementById('connectionBadge');
    if (label) label.innerText = text;
    if (badge) {
        badge.style.borderColor = color;
        const dot = badge.querySelector('.pulse-dot');
        if (dot) {
            dot.style.backgroundColor = color;
            dot.style.boxShadow = `0 0 8px ${color}`;
        }
    }
}

function handleWsMessage(msg) {
    if (!msg || !msg.type) return;

    switch (msg.type) {
        case 'INITIAL_SNAPSHOT':
            if (msg.data) {
                renderInitialSnapshot(msg.data);
            }
            break;

        case 'PRICE_UPDATE':
            if (msg.data) {
                handlePriceTick(msg.data);
            }
            break;

        case 'NEW_SIGNAL':
            if (msg.data) {
                handleNewSignal(msg.data, msg.stats);
            }
            break;

        case 'SIGNAL_TRIGGERED':
            if (msg.data) {
                handleSignalTriggered(msg.data);
            }
            break;

        case 'STATS_UPDATE':
            if (msg.stats) {
                updateStatsHeader(msg.stats);
            }
            break;
    }
}

// ==========================================================================
// 3. Render Handlers
// ==========================================================================
function renderInitialSnapshot(data) {
    // 1. Update Stats
    if (data.stats) {
        updateStatsHeader(data.stats);
    }

    // 2. Load Market Data
    if (Array.isArray(data.market)) {
        marketDataMap.clear();
        data.market.forEach(item => {
            marketDataMap.set(item.symbol, item);
        });
        renderMarketTable();
    }

    // 3. Load Signals
    if (Array.isArray(data.signals)) {
        signalsList = data.signals;
        renderSignalsTable();
    }
}

function handlePriceTick(tick) {
    const existing = marketDataMap.get(tick.symbol);
    if (existing) {
        const oldPrice = existing.ltp;
        existing.ltp = tick.ltp;
        existing.change_pct = tick.change_pct;
        if (tick.volume > 0) existing.volume = tick.volume;
        existing.time = tick.time;

        // Flash row if visible in Market tab
        const row = document.getElementById(`market-row-${tick.symbol}`);
        if (row) {
            updateMarketRowCells(row, existing, oldPrice);
        }

        const note = document.getElementById('lastUpdatedNote');
        if (note && tick.time) {
            note.innerText = `Last sync: ${tick.time}`;
        }
    }
}

function handleNewSignal(sig, stats) {
    signalsList.unshift(sig);
    if (stats) updateStatsHeader(stats);

    const isBull = (sig.direction || '').includes('BULLISH');
    playSignalChime(isBull);

    renderSignalsTable();
}

function handleSignalTriggered(updatedSig) {
    // Find existing signal and update status
    const match = signalsList.find(s => s.symbol === updatedSig.symbol && s.pattern === updatedSig.pattern && (s.timestamp === updatedSig.timestamp || !updatedSig.timestamp));
    if (match) {
        match.trigger_status = updatedSig.trigger_status;
        match.trigger_time = updatedSig.trigger_time;
    }
    renderSignalsTable();
}

function updateStatsHeader(stats) {
    if (stats.symbols_scanned !== undefined) {
        document.getElementById('statSymbols').innerText = stats.symbols_scanned;
        document.getElementById('marketCountBadge').innerText = stats.symbols_scanned;
    }
    if (stats.candles_processed !== undefined) {
        document.getElementById('statCandles').innerText = stats.candles_processed;
    }
    if (stats.bullish_signals !== undefined) {
        document.getElementById('statBullish').innerText = stats.bullish_signals;
    }
    if (stats.bearish_signals !== undefined) {
        document.getElementById('statBearish').innerText = stats.bearish_signals;
    }
    if (stats.patterns_detected !== undefined) {
        document.getElementById('statPatterns').innerText = stats.patterns_detected;
    }
    if (stats.last_updated) {
        const note = document.getElementById('lastUpdatedNote');
        if (note) note.innerText = `Last sync: ${stats.last_updated}`;
    }
}

// ==========================================================================
// 4. Table Builders
// ==========================================================================
let signalFilterDirection = 'ALL';
let signalFilterTimeframe = 'ALL';
let signalFilterPattern = 'ALL';
let signalFilterStatus = 'TRIGGERED';
let signalFilterScore = '7';
let signalFilterCpr = 'PROFITABLE';
let marketFilterCpr = 'ALL';
let searchQuery = '';

function applySignalFilters() {
    signalFilterDirection = document.getElementById('signalDirectionFilter').value;
    const tfEl = document.getElementById('signalTimeframeFilter');
    signalFilterTimeframe = tfEl ? tfEl.value : 'ALL';
    signalFilterPattern = document.getElementById('signalPatternFilter').value;
    const statusEl = document.getElementById('signalStatusFilter');
    signalFilterStatus = statusEl ? statusEl.value : 'ALL';
    const scoreEl = document.getElementById('signalScoreFilter');
    signalFilterScore = scoreEl ? scoreEl.value : 'ALL';
    const cprEl = document.getElementById('signalCprFilter');
    signalFilterCpr = cprEl ? cprEl.value : 'ALL';
    renderSignalsTable();
}

function renderSignalsTable() {
    const tbody = document.getElementById('signalsTableBody');
    if (!tbody) return;

    let filtered = signalsList.filter(sig => {
        // Filter by Direction
        if (signalFilterDirection !== 'ALL') {
            if (!sig.direction.includes(signalFilterDirection)) return false;
        }
        // Filter by Timeframe
        if (signalFilterTimeframe !== 'ALL') {
            const sigTf = (sig.timeframe || '5m').toLowerCase();
            if (sigTf !== signalFilterTimeframe.toLowerCase()) return false;
        }
        // Filter by Pattern
        if (signalFilterPattern !== 'ALL') {
            if (sig.pattern !== signalFilterPattern) return false;
        }
        // Filter by Trigger Status
        if (signalFilterStatus !== 'ALL') {
            const trigStatus = (sig.trigger_status || 'PENDING').toUpperCase();
            if (trigStatus !== signalFilterStatus) return false;
        }
        // Filter by Score (Option 2)
        if (signalFilterScore !== 'ALL') {
            const minScore = parseInt(signalFilterScore, 10);
            if ((sig.score || 0) < minScore) return false;
        }
        // Filter by CPR / Trap (Option 3 & Strict Zones & Top Profitable)
        const isBull = (sig.direction || '').includes('BULLISH');
        const zoneStr = (sig.zone || '');
        const conds = sig.conditions_met || [];
        if (signalFilterCpr === 'PROFITABLE') {
            const isProfitable = zoneStr.includes('Bear Trap Breakout') || conds.some(c => c.includes('Bear Trap Breakout')) ||
                                 zoneStr.includes('CPR Breakdown') || conds.some(c => c.includes('CPR Breakdown')) ||
                                 zoneStr.includes('CPR Resistance') || conds.some(c => c.includes('CPR Resistance')) ||
                                 zoneStr.includes('Narrow CPR') || conds.some(c => c.includes('Narrow CPR')) ||
                                 (zoneStr.includes('Narrow') && zoneStr.includes('Trap'));
            if (!isProfitable) return false;
        } else if (signalFilterCpr === 'STRICT') {
            const isStrict = zoneStr.includes('CPR') || zoneStr.includes('S2') || zoneStr.includes('R2') || zoneStr.includes('Trap') ||
                             conds.some(c => c.includes('CPR') || c.includes('S2') || c.includes('R2') || c.includes('Trap'));
            if (!isStrict) return false;
        } else if (signalFilterCpr === 'NARROW') {
            const hasNarrow = conds.some(c => c.includes('Narrow CPR')) || zoneStr.includes('Narrow CPR');
            if (!hasNarrow) return false;
        } else if (signalFilterCpr === 'TRAP') {
            if (!zoneStr.includes('Trap')) return false;
        } else if (signalFilterCpr === 'S2') {
            if (!isBull) return false;
            const hasS2 = conds.some(c => c.includes('S2 Support')) || zoneStr.includes('S2');
            if (!hasS2) return false;
        } else if (signalFilterCpr === 'R2') {
            if (isBull) return false;
            const hasR2 = conds.some(c => c.includes('R2 Resistance')) || zoneStr.includes('R2');
            if (!hasR2) return false;
        }

        // Search Query
        if (searchQuery) {
            const q = searchQuery.toUpperCase();
            return (sig.symbol || '').includes(q) || (sig.pattern || '').includes(q);
        }
        return true;
    });

    if (filtered.length === 0) {
        tbody.innerHTML = `<tr class="empty-row"><td colspan="15">No matching signals detected.</td></tr>`;
        return;
    }

    tbody.innerHTML = filtered.map(sig => {
        const isBull = (sig.direction || '').includes('BULLISH');
        const badgeClass = isBull ? 'badge-bullish' : 'badge-bearish';
        const scoreVal = sig.score || 0;
        let scoreClass = 'score-mid';
        if (scoreVal >= 7) scoreClass = 'score-high';
        else if (scoreVal <= 0) scoreClass = 'score-bearish';

        const condTags = (sig.conditions_met || []).map(c => {
            const isNarrow = c.includes('Narrow CPR');
            const isTrap = c.includes('Trap');
            const tagClass = isNarrow ? 'tag tag-narrow' : isTrap ? 'tag tag-trap' : 'tag';
            return `<span class="${tagClass}">${c}</span>`;
        }).join('');

        let timeDisplay = sig.timestamp || '--';
        if (typeof timeDisplay === 'string' && timeDisplay.includes('T')) {
            const parts = timeDisplay.split('T');
            if (parts.length > 1) {
                timeDisplay = parts[1].split('+')[0].split('.')[0].split('Z')[0];
            }
        }

        // Status badge formatting
        const trigStatus = (sig.trigger_status || 'PENDING').toUpperCase();
        const trigTime = sig.trigger_time || '';
        const trigPrice = Number(sig.trigger_price || (isBull ? sig.candle_high : sig.candle_low) || sig.price).toFixed(2);
        let statusBadge = '';
        if (trigStatus === 'TRIGGERED') {
            statusBadge = `<span class="badge badge-triggered">✅ TRIGGERED${trigTime ? ' (' + trigTime + ')' : ''}</span>`;
        } else if (trigStatus === 'INVALIDATED') {
            statusBadge = `<span class="badge badge-invalidated">❌ INVALIDATED</span>`;
        } else {
            const comp = isBull ? '>' : '<';
            statusBadge = `<span class="badge badge-pending">⏳ PENDING (${comp}${trigPrice})</span>`;
        }

        const tfVal = (sig.timeframe || '5m').toUpperCase();
        let tfBadgeClass = 'badge-tf-5m';
        if (tfVal === '3M') tfBadgeClass = 'badge-tf-3m';
        else if (tfVal === '15M') tfBadgeClass = 'badge-tf-15m';

        const liqTier = sig.liquidity_tier || 'Normal';
        let liqBadge = `<span class="badge badge-liquid-norm">${liqTier}</span>`;
        if (liqTier.includes('Ultra') || sig.is_most_liquid) {
            liqBadge = `<span class="badge badge-liquid-ultra">🔥 ULTRA</span>`;
        } else if (liqTier.includes('High')) {
            liqBadge = `<span class="badge badge-liquid-high">💧 HIGH</span>`;
        }
        const futContract = sig.fut_symbol || `${sig.symbol} FUT`;
        const lotSize = sig.lot_size ? `<span class="badge-lot">(${sig.lot_size})</span>` : '';
        const rowHighlightClass = (sig.is_most_liquid || liqTier.includes('Ultra')) ? 'row-highlight-liquid' : '';

        return `
            <tr class="${rowHighlightClass}">
                <td class="mono"><strong>${timeDisplay}</strong></td>
                <td><strong>${sig.symbol}</strong></td>
                <td style="text-align: center;">${liqBadge}</td>
                <td><span class="mono">${futContract}</span> ${lotSize}</td>
                <td style="text-align: center;"><span class="badge ${tfBadgeClass}">${tfVal}</span></td>
                <td><span class="badge ${badgeClass}">${sig.direction}</span></td>
                <td><span class="badge badge-pattern">${sig.pattern}</span></td>
                <td style="text-align: center;">${statusBadge}</td>
                <td class="num"><strong>${formatNumber(sig.price)}</strong></td>
                <td class="num"><span class="badge-score ${scoreClass}">${scoreVal}</span></td>
                <td><span class="badge badge-zone">${sig.zone || '--'}</span></td>
                <td class="num">${formatNumber(sig.pivot || sig.pp)}</td>
                <td class="num">${formatNumber(sig.pdh)}</td>
                <td class="num">${formatNumber(sig.pdl)}</td>
                <td class="num">${formatNumber(sig.r1)}</td>
                <td class="num">${formatNumber(sig.s1)}</td>
                <td class="num">${formatNumber(sig.relative_volume)}x</td>
                <td><div class="tag-list">${condTags || '<span class="tag">Standard Setup</span>'}</div></td>
            </tr>
        `;
    }).join('');
}

function renderMarketTable() {
    const tbody = document.getElementById('marketTableBody');
    if (!tbody) return;

    const mCprEl = document.getElementById('marketCprFilter');
    marketFilterCpr = mCprEl ? mCprEl.value : 'ALL';

    const items = Array.from(marketDataMap.values()).filter(item => {
        // CPR Filter
        if (marketFilterCpr === 'NARROW') {
            if ((item.cpr_width_pct || 0) > 0.20) return false;
        } else if (marketFilterCpr === 'TRAP') {
            if (!(item.zone || '').includes('Trap')) return false;
        } else if (marketFilterCpr === 'S2') {
            if (!(item.zone || '').includes('S2')) return false;
        } else if (marketFilterCpr === 'R2') {
            if (!(item.zone || '').includes('R2')) return false;
        }

        if (searchQuery) {
            return (item.symbol || '').includes(searchQuery.toUpperCase());
        }
        return true;
    });

    if (items.length === 0) {
        tbody.innerHTML = `<tr class="empty-row"><td colspan="24">No instruments found matching criteria.</td></tr>`;
        return;
    }

    tbody.innerHTML = items.map(item => {
        const chgVal = item.change_pct || 0;
        const chgClass = chgVal > 0 ? 'val-pos' : chgVal < 0 ? 'val-neg' : '';
        const chgPrefix = chgVal > 0 ? '+' : '';
        const cprWidth = item.cpr_width_pct || 0;
        const isNarrow = cprWidth <= 0.20;

        let cprDisplay = `${formatNumber(cprWidth)}%`;
        if (isNarrow) {
            cprDisplay = `<span class="badge badge-narrow">⚡ ${formatNumber(cprWidth)}% (Narrow)</span>`;
        }

        const liqTier = item.liquidity_tier || 'Normal';
        let liqBadge = `<span class="badge badge-liquid-norm">${liqTier}</span>`;
        if (liqTier.includes('Ultra') || item.is_most_liquid) {
            liqBadge = `<span class="badge badge-liquid-ultra">🔥 ULTRA</span>`;
        } else if (liqTier.includes('High')) {
            liqBadge = `<span class="badge badge-liquid-high">💧 HIGH</span>`;
        }
        const futContract = item.fut_symbol || `${item.symbol} FUT`;
        const lotVal = item.lot_size || '--';
        const turnoverVal = item.turnover_cr ? `₹${formatNumber(item.turnover_cr)} Cr` : '--';
        const rowHighlightClass = (item.is_most_liquid || liqTier.includes('Ultra')) ? 'row-highlight-liquid' : '';

        return `
            <tr id="market-row-${item.symbol}" class="${rowHighlightClass}">
                <td><strong>${item.symbol}</strong></td>
                <td style="text-align: center;">${liqBadge}</td>
                <td><span class="mono">${futContract}</span></td>
                <td class="num">${lotVal}</td>
                <td class="num"><strong>${turnoverVal}</strong></td>
                <td class="num cell-ltp"><strong>${formatNumber(item.ltp)}</strong></td>
                <td class="num cell-chg ${chgClass}"><strong>${chgPrefix}${formatNumber(chgVal)}%</strong></td>
                <td class="num cell-vol">${formatVolume(item.volume)}</td>
                <td><span class="badge badge-zone">${item.zone || '--'}</span></td>
                <td class="num"><strong>${formatNumber(item.pp)}</strong></td>
                <td class="num">${formatNumber(item.tc)}</td>
                <td class="num">${formatNumber(item.bc)}</td>
                <td class="num">${cprDisplay}</td>
                <td class="num">${formatNumber(item.r1)}</td>
                <td class="num">${formatNumber(item.r2)}</td>
                <td class="num">${formatNumber(item.r3)}</td>
                <td class="num">${formatNumber(item.s1)}</td>
                <td class="num">${formatNumber(item.s2)}</td>
                <td class="num">${formatNumber(item.s3)}</td>
                <td class="num">${formatNumber(item.pdo)}</td>
                <td class="num">${formatNumber(item.pdh)}</td>
                <td class="num">${formatNumber(item.pdl)}</td>
                <td class="num">${formatNumber(item.pdc)}</td>
                <td class="mono cell-time">${item.time || '--'}</td>
            </tr>
        `;
    }).join('');
}

function updateMarketRowCells(row, item, oldPrice) {
    const ltpCell = row.querySelector('.cell-ltp');
    const chgCell = row.querySelector('.cell-chg');
    const volCell = row.querySelector('.cell-vol');
    const timeCell = row.querySelector('.cell-time');

    if (ltpCell) {
        ltpCell.innerHTML = `<strong>${formatNumber(item.ltp)}</strong>`;
        if (oldPrice && item.ltp !== oldPrice) {
            row.classList.remove('flash-up', 'flash-down');
            void row.offsetWidth; // Trigger reflow
            row.classList.add(item.ltp > oldPrice ? 'flash-up' : 'flash-down');
        }
    }
    if (chgCell) {
        const chgVal = item.change_pct || 0;
        chgCell.className = `num cell-chg ${chgVal > 0 ? 'val-pos' : chgVal < 0 ? 'val-neg' : ''}`;
        const chgPrefix = chgVal > 0 ? '+' : '';
        chgCell.innerHTML = `<strong>${chgPrefix}${formatNumber(chgVal)}%</strong>`;
    }
    if (volCell && item.volume > 0) {
        volCell.innerText = formatVolume(item.volume);
    }
    if (timeCell && item.time) {
        timeCell.innerText = item.time;
    }
}

// ==========================================================================
// 5. User Interaction (Tabs, Search, Filters, CSV Export)
// ==========================================================================
function switchTab(tabName) {
    currentTab = tabName;
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));

    const signalFilters = document.getElementById('signalFilters');
    const marketFilters = document.getElementById('marketFilters');

    if (tabName === 'signals') {
        document.getElementById('tabBtnSignals').classList.add('active');
        document.getElementById('tabSignals').classList.add('active');
        if (signalFilters) signalFilters.style.display = 'flex';
        if (marketFilters) marketFilters.style.display = 'none';
        renderSignalsTable();
    } else {
        document.getElementById('tabBtnMarket').classList.add('active');
        document.getElementById('tabMarket').classList.add('active');
        if (signalFilters) signalFilters.style.display = 'none';
        if (marketFilters) marketFilters.style.display = 'flex';
        renderMarketTable();
    }
}

function handleSearch(val) {
    searchQuery = val.trim();
    if (currentTab === 'signals') {
        renderSignalsTable();
    } else {
        renderMarketTable();
    }
}

function exportCurrentTableToCSV() {
    let filename = currentTab === 'signals' ? 'fno_reversal_signals.csv' : 'fno_market_pivots.csv';
    let rows = [];

    if (currentTab === 'signals') {
        rows.push(['Time', 'Symbol', 'Liquidity', 'Fut Contract', 'Signal', 'Pattern', 'Price', 'Score', 'Pivot Zone', 'Pivot (PP)', 'PDH', 'PDL', 'R1', 'S1', 'Rel Vol', 'Conditions & Factors Met']);
        signalsList.forEach(s => {
            rows.push([
                s.timestamp, s.symbol, s.liquidity_tier || 'Normal', s.fut_symbol || '', s.direction, s.pattern, s.price, s.score,
                `"${s.zone || ''}"`, s.pivot || s.pp, s.pdh, s.pdl, s.r1, s.s1, s.relative_volume,
                `"${(s.conditions_met || []).join('; ')}"`
            ]);
        });
    } else {
        rows.push(['Symbol', 'Liquidity', 'Fut Contract', 'Lot Size', 'Turnover (Cr)', 'LTP', 'Change %', 'Volume', 'Pivot Zone', 'Pivot (PP)', 'TC', 'BC', 'CPR Width %', 'R1', 'R2', 'R3', 'S1', 'S2', 'S3', 'PDO', 'PDH', 'PDL', 'PDC', 'Last Updated']);
        marketDataMap.forEach(m => {
            rows.push([
                m.symbol, m.liquidity_tier || 'Normal', m.fut_symbol || '', m.lot_size || '', m.turnover_cr || 0, m.ltp, m.change_pct, m.volume, `"${m.zone || ''}"`, m.pp, m.tc, m.bc, `${m.cpr_width_pct}%`,
                m.r1, m.r2, m.r3, m.s1, m.s2, m.s3, m.pdo, m.pdh, m.pdl, m.pdc, m.time
            ]);
        });
    }

    const csvContent = "data:text/csv;charset=utf-8," + rows.map(e => e.join(",")).join("\n");
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", filename);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}

function formatNumber(num) {
    if (num === null || num === undefined || isNaN(num)) return '--';
    return Number(num).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatVolume(vol) {
    if (!vol || isNaN(vol)) return '0';
    return Number(vol).toLocaleString('en-IN');
}
