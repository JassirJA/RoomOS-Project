/*
 * ROOM OS — Frontend Client Controller
 * "Don't watch the room. Sense the room."
 */

class RoomOSClient {
  constructor() {
    this.ws = null;
    this.canvas = document.getElementById('csi-canvas');
    this.ctx = this.canvas.getContext('2d');
    this.subcarrierHistory = [];
    this.maxHistory = 40;

    this.initElements();
    this.initClock();
    this.initEvents();
    this.connectWebSocket();
    this.fetchMemoryLog();
  }

  initElements() {
    this.valPresence = document.getElementById('val-presence');
    this.valOccupancy = document.getElementById('val-occupancy');
    this.valActivity = document.getElementById('val-activity');
    this.valDuration = document.getElementById('val-duration');
    this.valMotion = document.getElementById('val-motion');
    this.fillMotion = document.getElementById('fill-motion');
    this.valStability = document.getElementById('val-stability');
    this.fillStability = document.getElementById('fill-stability');
    this.valConfidence = document.getElementById('val-confidence');
    this.statusPill = document.getElementById('room-status-pill');

    this.subConfSignal = document.getElementById('sub-conf-signal');
    this.subConfBase = document.getElementById('sub-conf-base');
    this.subConfAct = document.getElementById('sub-conf-act');

    this.metaSubcarriers = document.getElementById('meta-subcarriers');
    this.metaDisturbance = document.getElementById('meta-disturbance');
    this.distMeterFill = document.getElementById('dist-meter-fill');
    this.distVal = document.getElementById('dist-val');

    this.whyContent = document.getElementById('why-content');
    this.evidenceStatus = document.getElementById('evidence-status');
    this.timelineList = document.getElementById('timeline-list');
    this.memoryLogList = document.getElementById('memory-log-list');

    this.sensorBadge = document.getElementById('sensor-badge');
    this.sensorStatusLabel = document.getElementById('sensor-status-label');
    this.healthPktRate = document.getElementById('health-pkt-rate');
    this.healthRssi = document.getElementById('health-rssi');
    this.healthOverall = document.getElementById('health-overall');
    this.healthTx = document.getElementById('health-tx');
    this.healthRx = document.getElementById('health-rx');
    this.healthBaseline = document.getElementById('health-baseline');

    this.calibOverlay = document.getElementById('calibration-overlay');
    this.calibProgressFill = document.getElementById('calib-progress-fill');
    this.calibStats = document.getElementById('calib-stats');
    this.btnCalibrate = document.getElementById('btn-calibrate');

    // Patent Telemetry Elements
    this.displacementAlert = document.getElementById('displacement-alert');
    this.valGate = document.getElementById('val-gate');
    this.valProbe = document.getElementById('val-probe');
    this.valSubcarriers = document.getElementById('val-subcarriers');
    this.valDrift = document.getElementById('val-drift');
    this.valStateVector = document.getElementById('val-state-vector');
  }

  initClock() {
    const updateTime = () => {
      const now = new Date();
      document.getElementById('system-clock').textContent = now.toTimeString().split(' ')[0];
    };
    setInterval(updateTime, 1000);
    updateTime();
  }

  initEvents() {
    this.btnCalibrate.addEventListener('click', () => {
      this.triggerCalibration();
    });

    // Mock activity controls
    document.querySelectorAll('.btn-mock[data-act]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        document.querySelectorAll('.btn-mock[data-act]').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const act = btn.dataset.act;
        this.setMockActivity(act);
      });
    });

    // Scenario triggers
    const btnFall = document.getElementById('btn-scenario-fall');
    if (btnFall) {
      btnFall.addEventListener('click', () => {
        this.triggerScenario('FALL');
      });
    }

    const btnDisc = document.getElementById('btn-scenario-disc');
    if (btnDisc) {
      btnDisc.addEventListener('click', () => {
        this.triggerScenario('DISCONNECT');
      });
    }

    const btnRec = document.getElementById('btn-scenario-rec');
    if (btnRec) {
      btnRec.addEventListener('click', () => {
        this.triggerScenario('RECONNECT');
      });
    }
  }

  triggerScenario(name) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ action: 'scenario', name: name }));
    } else {
      fetch('/api/mock/scenario', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scenario: name })
      }).catch(e => console.error(e));
    }
  }

  connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    this.ws = new WebSocket(wsUrl);

    this.ws.onopen = () => {
      console.log('Room OS WebSocket connected');
      this.updateSensorHealth(true, 'ESP32 RX: ONLINE');
    };

    this.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'TELEMETRY') {
          this.handleTelemetry(msg);
        } else if (msg.type === 'EVENT') {
          this.handleSystemEvent(msg);
        }
      } catch (e) {
        console.error('Error parsing WS message:', e);
      }
    };

    this.ws.onclose = () => {
      this.updateSensorHealth(false, 'ESP32 RX: DISCONNECTED');
      setTimeout(() => this.connectWebSocket(), 2000);
    };

    this.ws.onerror = () => {
      this.ws.close();
    };
  }

  handleTelemetry(data) {
    const state = data.state;
    if (!state) return;

    // Presence & Occupancy
    if (state.sensor_status !== 'ONLINE') {
      this.valPresence.textContent = 'NO SENSOR';
      this.valPresence.style.color = 'var(--text-dim)';
      this.valOccupancy.textContent = 'ESP32 Unplugged';
      this.statusPill.textContent = 'DISCONNECTED';
      this.statusPill.className = 'status-pill alert';
      this.valActivity.textContent = 'OFFLINE';
      this.valActivity.style.color = 'var(--text-muted)';
      this.valMotion.textContent = '0.00';
      this.fillMotion.style.width = '0%';
      this.valStability.textContent = '1.00';
      this.fillStability.style.width = '100%';
      this.valConfidence.textContent = '0%';
    } else {
      this.valPresence.textContent = state.presence ? 'DETECTED' : 'CLEAR';
      this.valPresence.style.color = state.presence ? 'var(--accent-green)' : 'var(--text-dim)';
      this.valOccupancy.textContent = `Occupancy: ${state.occupancy}`;

      if (state.anomaly) {
        this.statusPill.textContent = 'ANOMALY ALERT';
        this.statusPill.className = 'status-pill alert';
      } else if (state.confidence_breakdown?.is_uncertain) {
        this.statusPill.textContent = 'UNCERTAIN';
        this.statusPill.className = 'status-pill uncertain';
      } else {
        this.statusPill.textContent = 'ONLINE';
        this.statusPill.className = 'status-pill';
      }

      // Activity display
      let actText = state.activity.replace(/_/g, ' ');
      if (state.activity === 'HAND_MOVEMENT' || state.activity === 'WIGGLING_FINGERS') {
        actText = 'HAND MOVEMENT DETECTED';
        this.valActivity.style.color = '#38bdf8';
      } else if (state.activity === 'HAND_WAVE') {
        actText = 'HAND WAVE / BIG MOTION';
        this.valActivity.style.color = '#f59e0b';
      } else if (state.activity === 'WALKING' || state.activity === 'FAST_MOVEMENT') {
        this.valActivity.style.color = 'var(--accent-cyan)';
      } else if (state.activity === 'SITTING' || state.activity === 'STANDING') {
        this.valActivity.style.color = 'var(--accent-green)';
      } else if (state.activity === 'NO_MOVEMENT') {
        actText = 'STILL / NO MOVEMENT';
        this.valActivity.style.color = 'var(--text-muted)';
      } else {
        this.valActivity.style.color = 'var(--text-muted)';
      }
      this.valActivity.textContent = actText;

      // Motion Level
      this.valMotion.textContent = Number(state.motion_level).toFixed(2);
      this.fillMotion.style.width = `${Math.min(100, state.motion_level * 100)}%`;

      // Room Stability
      this.valStability.textContent = Number(state.room_stability).toFixed(2);
      this.fillStability.style.width = `${Math.min(100, state.room_stability * 100)}%`;

      // Confidence
      const confPct = Math.round(state.confidence * 100);
      this.valConfidence.textContent = `${confPct}%`;
    }

    const confBreak = state.confidence_breakdown || {};
    this.subConfSignal.textContent = confBreak.signal_quality ? `${Math.round(confBreak.signal_quality * 100)}%` : '--';
    this.subConfBase.textContent = confBreak.baseline_similarity ? `${Math.round(confBreak.baseline_similarity * 100)}%` : '--';
    this.subConfAct.textContent = confBreak.activity_confidence ? `${Math.round(confBreak.activity_confidence * 100)}%` : '--';

    // Disturbance Meter
    const dist = state.baseline_deviation || 1.0;
    this.distVal.textContent = `${dist.toFixed(1)}x`;
    this.metaDisturbance.textContent = `Disturbance: ${dist.toFixed(1)}x`;
    const distPct = Math.min(100, (dist / 6.0) * 100);
    this.distMeterFill.style.width = `${distPct}%`;

    // Patent Innovation Telemetry
    if (this.displacementAlert) {
      this.displacementAlert.style.display = state.device_displaced ? 'flex' : 'none';
    }

    if (this.valGate) {
      const gateOpen = data.baseline_gate_open;
      this.valGate.textContent = gateOpen ? 'OPEN (ADAPTING)' : 'LOCKED (PROTECTED)';
      this.valGate.className = `p-val badge-gate ${gateOpen ? 'open' : 'locked'}`;
    }

    if (this.valProbe) {
      const probeState = data.probe_state || 'IDLE';
      this.valProbe.textContent = probeState === 'PROBING_ACTIVE' ? 'PROBING (200 Hz)' : `${probeState} (100 Hz)`;
      this.valProbe.className = `p-val badge-probe ${probeState === 'PROBING_ACTIVE' ? 'active' : ''}`;
    }

    if (this.valSubcarriers) {
      if (data.subcarrier_reliability) {
        this.valSubcarriers.textContent = `${data.subcarrier_reliability.reliable_count}/${data.subcarrier_reliability.total_subcarriers} Active`;
      }
    }

    if (this.valDrift) {
      const driftScore = (data.drift_score !== undefined) ? Number(data.drift_score).toFixed(3) : '0.000';
      const driftClass = data.drift_classification || 'STABLE';
      this.valDrift.textContent = `${driftScore} (${driftClass})`;
    }

    if (this.valStateVector && state.state_vector) {
      const v = state.state_vector.vector_representation;
      if (v) {
        this.valStateVector.textContent = `R_t=[${v[0]}, ${v[1]}, ${v[2]}, ${v[3]}, ${v[4]}, ${v[5]}, ${v[6]}]`;
      }
    }

    // Subcarriers Canvas
    if (data.subcarriers && data.subcarriers.length > 0) {
      this.metaSubcarriers.textContent = `${data.subcarriers.length} Subcarriers`;
      this.renderCSIWaveform(data.subcarriers);
    } else {
      this.metaSubcarriers.textContent = `0 Subcarriers`;
      this.renderCSIWaveform([]);
    }

    // Why Panel
    this.renderWhyPanel(data.active_anomaly, state);

    // Trajectory Timeline
    if (data.trajectory) {
      this.renderTrajectory(data.trajectory, state.activity);
    }

    // Health Breakdown
    const rxOnline = state.sensor_status === 'ONLINE';
    const txOnline = rxOnline && (state.packet_rate_hz > 0 || (state.rssi && state.rssi !== 0));

    if (this.healthRx) {
      this.healthRx.textContent = rxOnline ? 'ONLINE' : 'DISCONNECTED';
      this.healthRx.className = rxOnline ? 'health-val text-green' : 'health-val text-red';
    }
    if (this.healthTx) {
      this.healthTx.textContent = txOnline ? 'ONLINE' : (rxOnline ? 'NO TX SIGNAL' : 'OFFLINE');
      this.healthTx.className = txOnline ? 'health-val text-green' : 'health-val text-red';
    }
    if (this.healthBaseline) {
      const isBaseReady = state.confidence_breakdown?.is_baseline_ready;
      this.healthBaseline.textContent = isBaseReady ? 'CALIBRATED' : (rxOnline ? 'CALIBRATING...' : 'AWAITING HARDWARE');
      this.healthBaseline.className = isBaseReady ? 'health-val text-green' : 'health-val text-muted';
    }

    this.healthPktRate.textContent = `${(state.packet_rate_hz || 0).toFixed(1)} Hz`;
    this.healthRssi.textContent = rxOnline ? `${state.rssi || 0} dBm / -96 dBm` : '-- dBm / -- dBm';
    this.updateSensorHealth(rxOnline, `ESP32 RX: ${rxOnline ? 'ONLINE' : 'DISCONNECTED'}`);
  }

  renderCSIWaveform(amplitudes) {
    const w = this.canvas.width;
    const h = this.canvas.height;
    const ctx = this.ctx;

    ctx.fillStyle = '#05080e';
    ctx.fillRect(0, 0, w, h);

    // Draw subtle grid lines
    ctx.strokeStyle = 'rgba(45, 60, 88, 0.2)';
    ctx.lineWidth = 1;
    for (let y = 40; y < h; y += 40) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
      ctx.stroke();
    }

    if (!amplitudes || amplitudes.length === 0) {
      ctx.fillStyle = 'rgba(148, 163, 184, 0.6)';
      ctx.font = '13px "JetBrains Mono", monospace';
      ctx.textAlign = 'center';
      ctx.fillText('WAITING FOR ESP32 CSI STREAM...', w / 2, h / 2);
      return;
    }

    const n = amplitudes.length;
    const step = w / (n - 1);
    const maxVal = Math.max(30, Math.max(...amplitudes) * 1.15);

    // Gradient area fill
    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, 'rgba(56, 189, 248, 0.45)');
    grad.addColorStop(1, 'rgba(56, 189, 248, 0.0)');

    ctx.beginPath();
    ctx.moveTo(0, h);
    for (let i = 0; i < n; i++) {
      const x = i * step;
      const y = h - (amplitudes[i] / maxVal) * (h - 20);
      ctx.lineTo(x, y);
    }
    ctx.lineTo(w, h);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    // Top crisp line
    ctx.beginPath();
    for (let i = 0; i < n; i++) {
      const x = i * step;
      const y = h - (amplitudes[i] / maxVal) * (h - 20);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.strokeStyle = '#38bdf8';
    ctx.lineWidth = 2;
    ctx.shadowColor = '#38bdf8';
    ctx.shadowBlur = 8;
    ctx.stroke();
    ctx.shadowBlur = 0; // reset
  }

  renderWhyPanel(anomaly, state) {
    if (anomaly && anomaly.evidence) {
      this.evidenceStatus.textContent = 'ANOMALY DETECTED';
      this.evidenceStatus.style.color = 'var(--accent-red)';

      let itemsHtml = anomaly.evidence.map(e => `
        <div class="evidence-item">
          <span class="evidence-check">✓</span>
          <span><strong>${e.claim}</strong></span>
        </div>
      `).join('');

      this.whyContent.innerHTML = `
        <div class="evidence-box">
          <div class="evidence-title">
            <span>⚠ ${anomaly.type.replace(/_/g, ' ')}</span>
            <span style="font-size:0.75rem; color:var(--text-dim); margin-left:auto">Confidence: ${Math.round(anomaly.confidence * 100)}%</span>
          </div>
          ${itemsHtml}
        </div>
      `;
    } else if (state.confidence_breakdown?.is_uncertain) {
      this.evidenceStatus.textContent = 'UNCERTAIN';
      this.evidenceStatus.style.color = 'var(--accent-yellow)';
      this.whyContent.innerHTML = `
        <div class="evidence-box" style="border-color: rgba(245,158,11,0.3); background: rgba(245,158,11,0.06)">
          <div class="evidence-title" style="color: var(--accent-yellow)">
            <span>STATUS: UNCERTAIN</span>
          </div>
          <div class="evidence-item">
            <span class="evidence-check" style="color:var(--accent-yellow)">!</span>
            <span>${state.status_message || 'Signal quality degraded. Predictions suspended.'}</span>
          </div>
        </div>
      `;
    } else {
      this.evidenceStatus.textContent = 'NOMINAL';
      this.evidenceStatus.style.color = '#34d399';
      this.whyContent.innerHTML = `
        <div class="evidence-box nominal">
          <div class="evidence-title nominal">
            <span>✓ VERIFIED STABLE MULTIPATH</span>
          </div>
          <div class="evidence-item">
            <span class="evidence-check">✓</span>
            <span>Subcarrier amplitudes tracking inside baseline boundary.</span>
          </div>
          <div class="evidence-item">
            <span class="evidence-check">✓</span>
            <span>Active RF channel: Channel 11 (Direct ESP32 Link).</span>
          </div>
        </div>
      `;
    }
  }

  renderTrajectory(trajectory, currentAct) {
    if (!trajectory || trajectory.length === 0) return;

    let html = '';
    trajectory.forEach(t => {
      html += `
        <div class="timeline-item">
          <div class="time-marker"></div>
          <div class="time-content">
            <span class="state-name">${t.activity.replace(/_/g, ' ')}</span>
            <span class="state-meta">${t.duration}s</span>
          </div>
        </div>
      `;
    });

    html += `
      <div class="timeline-item active" style="border: 1px solid var(--accent-cyan-dim)">
        <div class="time-marker" style="background:var(--accent-cyan)"></div>
        <div class="time-content">
          <span class="state-name" style="color:var(--accent-cyan)">${currentAct.replace(/_/g, ' ')}</span>
          <span class="state-meta" style="color:var(--accent-cyan)">Current</span>
        </div>
      </div>
    `;

    this.timelineList.innerHTML = html;
  }

  handleSystemEvent(eventMsg) {
    const eventName = eventMsg.event;
    const data = eventMsg.data;

    // Append to memory log display
    const entry = document.createElement('div');
    entry.className = 'log-entry';
    const timeStr = new Date().toTimeString().split(' ')[0];
    entry.textContent = `${timeStr}  [${eventName}]`;
    this.memoryLogList.prepend(entry);

    if (eventName === 'CALIBRATION_STARTED') {
      this.calibOverlay.classList.add('active');
      this.calibProgressFill.style.width = '0%';
    } else if (eventName === 'CALIBRATION_PROGRESS') {
      const pct = Math.round((data.progress || 0) * 100);
      this.calibProgressFill.style.width = `${pct}%`;
      this.calibStats.textContent = `Learning static multipath... ${pct}%`;
    } else if (eventName === 'CALIBRATION_COMPLETED' || eventName === 'BASELINE_READY') {
      this.calibProgressFill.style.width = '100%';
      this.calibStats.textContent = 'ROOM BASELINE READY';
      setTimeout(() => {
        this.calibOverlay.classList.remove('active');
      }, 700);
    } else if (eventName === 'CALIBRATION_FAILED') {
      this.calibStats.textContent = 'CALIBRATION TIMED OUT (NO CSI PACKETS FROM TX)';
      this.calibProgressFill.style.backgroundColor = '#ef4444';
      setTimeout(() => {
        this.calibOverlay.classList.remove('active');
        this.calibProgressFill.style.backgroundColor = '';
      }, 2500);
    }
  }

  updateSensorHealth(isOnline, label) {
    this.sensorStatusLabel.textContent = label;
    const dot = this.sensorBadge.querySelector('.status-dot');
    if (isOnline) {
      dot.className = 'status-dot';
      this.healthOverall.textContent = 'OPTIMAL';
      this.healthOverall.className = 'health-indicator good';
    } else {
      dot.className = 'status-dot offline';
      this.healthOverall.textContent = 'DEGRADED';
      this.healthOverall.className = 'health-indicator';
    }
  }

  triggerCalibration() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ action: 'calibrate', duration: 1.0 }));
    }
  }

  setMockActivity(act) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ action: 'set_activity', activity: act }));
    }
  }

  async fetchMemoryLog() {
    try {
      const resp = await fetch('/api/events?limit=15');
      if (resp.ok) {
        const events = await resp.json();
        if (events && events.length > 0) {
          this.memoryLogList.innerHTML = events.map(e => {
            const date = new Date(e.timestamp * 1000);
            const timeStr = date.toTimeString().split(' ')[0];
            return `<div class="log-entry">${timeStr}  [${e.event_type}]</div>`;
          }).join('');
        }
      }
    } catch (e) {
      console.warn('Could not fetch historical memory log:', e);
    }
  }
}

window.addEventListener('DOMContentLoaded', () => {
  new RoomOSClient();
});
