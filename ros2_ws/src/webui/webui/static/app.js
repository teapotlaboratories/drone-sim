/* drone-sim hand-flying interface — the browser half.                          (SIM-45)
 *
 * Everything here is ROS 2 through rosbridge, plus two <img> tags pointed at
 * web_video_server. There is no application server: the page is served by
 * `python3 -m http.server` out of this directory and speaks to the graph directly.
 *
 * WHAT THIS PAGE IS ALLOWED TO DO. rosbridge is launched with
 * topics_pub_glob = ['/mission/command'], enforced in
 * rosbridge_library/capabilities/publish.py before the topic registration is created. So the
 * publish below is the only write that exists, and a mistake here cannot reach
 * /fmu/in/vehicle_command. That is a property of the launch file, not of this script's good
 * behaviour — see webui.launch.py.
 *
 * WHY EVERY READ IS DEFENSIVE. px4_msgs field names are branch-matched to PX4 v1.16.0 and
 * have moved between releases (vehicle_status vs vehicle_status_v1 is one this repo already
 * carries). A field that is absent must render as an em dash beside its topic name, never as
 * 0.00 — a plausible zero on a survey tool is how you fly into something.
 */
'use strict';

const PORTS = { rosbridge: 9090, video: 8181 };
const HOST = window.location.hostname || '127.0.0.1';

// Subscriptions are throttled server-side. /fmu/out/sensor_combined runs near 300 Hz and
// vehicle_local_position well above what an eye can read; pushing either at full rate down a
// websocket to a browser buys nothing and competes with the renderer for CPU.
const THROTTLE_MS = 100;   // 10 Hz, which is faster than anyone can act on

const el = (id) => document.getElementById(id);
const fmt = (v, digits = 2, unit = '') =>
  (v === undefined || v === null || !isFinite(v)) ? null : v.toFixed(digits) + unit;

// COMPOSE MULTI-PART VALUES THROUGH THIS, never through a bare template literal.
//
// `${fmt(a)} / ${fmt(b)}` interpolates a null as the STRING "null", so a missing component
// rendered as `1.0 / null / 2.0` -- and because that is a non-null string, put() treated it as
// a measurement and did not mark it stale. That breaks this file's own rule at the top: a
// field that did not arrive must read as an em dash, never as a plausible value. (review)
const join = (parts, sep = ' / ') =>
  parts.some((p) => p === null || p === undefined) ? null : parts.join(sep);

/** Write a value, or mark the field as never-arrived. Never prints a plausible zero. */
function put(id, value) {
  const node = el(id);
  if (!node) return;
  if (value === null || value === undefined) {
    node.textContent = '—';
    node.classList.add('stale');
  } else {
    node.textContent = value;
    node.classList.remove('stale');
  }
}

/* ------------------------------------------------------------------ connection */

const ros = new ROSLIB.Ros({ url: `ws://${HOST}:${PORTS.rosbridge}` });
let connected = false;

function setLink(up, text) {
  connected = up;
  const node = el('link');
  node.textContent = 'rosbridge: ' + text;
  node.className = 'link ' + (up ? 'link-up' : 'link-down');
  refreshButtons();
}

ros.on('connection', () => { setLink(true, `connected ${HOST}:${PORTS.rosbridge}`); say('connected'); });
ros.on('close', () => { setLink(false, 'disconnected'); say('rosbridge closed — is the webui still running in sim-webui?'); });
ros.on('error', () => setLink(false, 'error'));

function say(text, refused) {
  const line = document.createElement('div');
  if (refused) line.className = 'refused';
  line.textContent = new Date().toLocaleTimeString() + '  ' + text;
  const log = el('log');
  log.prepend(line);
  while (log.childElementCount > 8) log.lastElementChild.remove();
}

/* ---------------------------------------------------------------------- cameras */

/* web_video_server's `ros_compressed` streamer serves a CompressedImage topic with NO
 * transcode — it forwards the JPEG bytes the publisher already produced. Both cameras here
 * are already JPEG (the chase camera encodes in ffmpeg; airsim_node's come from
 * compressed_image_transport, measured at 15–17 Hz), so nothing re-encodes anywhere and the
 * renderer's GPU is left alone.
 *
 * The topic parameter is the image_transport BASE name; the streamer appends /compressed. */
function stream(topic) {
  // THE SLASHES MUST NOT BE PERCENT-ENCODED.
  //
  // web_video_server does NOT url-decode the `topic` query parameter -- it passes the raw
  // string to the ROS name validator, which rejects `%`:
  //
  //   Invalid topic name: topic name must not contain characters other than alphanumerics,
  //   '_', '~', '{', or '}':  '%2Fchase%2Fimage'
  //
  // A plain `encodeURIComponent(topic)` therefore breaks every camera, and it breaks them
  // QUIETLY: the server still answers HTTP 200, writes the multipart boundary, and closes.
  // The browser sees a 22-byte 200 and an <img> that never decodes, so the page shows an
  // empty pane -- indistinguishable from "no camera is publishing". Measured: 22 bytes from
  // the page against 100 frames / 10 s from curl on the same topic, which is what finally
  // separated the two.
  //
  // Slashes are legal in a query VALUE (RFC 3986 §3.4), so leaving them raw is correct rather
  // than a workaround. Everything else is still encoded, so a topic containing a space or a
  // `&` cannot break out of the parameter.
  const t = encodeURIComponent(topic).replace(/%2F/g, "/");
  return `http://${HOST}:${PORTS.video}/stream?topic=${t}&type=ros_compressed`;
}

function mountCamera(imgId, fbId, topic) {
  const img = el(imgId), fb = el(fbId);

  // POLL naturalWidth; DO NOT rely on the `load` event.
  //
  // A `multipart/x-mixed-replace` response never finishes, and the `load` event's timing for
  // one is not something browsers agree on -- Chrome 124 headless never fired it here, so the
  // pane stayed `display:none` and showed the "no stream" fallback while frames were arriving
  // perfectly well underneath (measured: 10 fps, correct multipart framing, JPEG decodable by
  // hand). Gating the reveal on an event that may never come is the bug; `naturalWidth`
  // becomes non-zero as soon as a frame is decoded, which is the thing actually being waited
  // for.
  //
  // `error` is still worth listening to -- it fires on a refused connection, which is the
  // "web_video_server is not running" case and is worth distinguishing from "no frames yet".
  let live = false;
  const check = () => {
    const ok = img.naturalWidth > 0;
    if (ok !== live) {
      live = ok;
      img.classList.toggle('live', ok);
      fb.classList.toggle('hidden', ok);
    }
  };
  img.onerror = () => { live = false; img.classList.remove('live'); fb.classList.remove('hidden'); };
  img.src = stream(topic);
  setInterval(check, 500);
  check();
}

/* ------------------------------------------------------------------ spotlight (SIM-49) */

// Which view is large. Changed by clicking a thumbnail or pressing 1/2/3.
//
// THE SWAP IS PURELY A CLASS CHANGE. Nothing here touches `src` and nothing moves an element
// in the DOM: both would tear down the multipart stream and re-open it, so the view you just
// asked to see would go blank for about a second. CSS grid places whichever figure carries
// `.spot`; the other two fall into the sidebar in DOM order.
const CAMS = ['chase', 'drone', 'depth'];
const SPOT_KEY = 'drone-sim.spotlight';

function setSpotlight(cam) {
  if (!CAMS.includes(cam)) return;
  const views = el('views');
  views.dataset.spot = cam;
  for (const fig of views.querySelectorAll('figure[data-cam]')) {
    const isSpot = fig.dataset.cam === cam;
    fig.classList.toggle('spot', isSpot);
    fig.classList.toggle('thumb', !isSpot);
    // A thumbnail is a control; the spotlight is not. Screen readers and keyboard users get
    // the same distinction the cursor already makes.
    fig.setAttribute('role', isSpot ? 'img' : 'button');
    fig.setAttribute('aria-label', isSpot ? `${cam} camera, shown large`
                                          : `show the ${cam} camera large`);
  }
  // Remembered per browser, so an operator who prefers the depth view does not re-pick it on
  // every reload. Wrapped because a private window or blocked site data throws on access.
  try { localStorage.setItem(SPOT_KEY, cam); } catch (e) { /* not important enough to fail */ }
}

for (const fig of document.querySelectorAll('#views figure[data-cam]')) {
  fig.addEventListener('click', () => setSpotlight(fig.dataset.cam));
  fig.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); setSpotlight(fig.dataset.cam); }
  });
}

// 1 / 2 / 3. Deliberately not letters: W A S D, R F and Q E already fly the aircraft, and a
// mis-hit there moves it rather than changing a layout.
window.addEventListener('keydown', (ev) => {
  if (ev.target.tagName === 'INPUT' || ev.metaKey || ev.ctrlKey || ev.altKey) return;
  const i = ['1', '2', '3'].indexOf(ev.key);
  if (i >= 0) { ev.preventDefault(); setSpotlight(CAMS[i]); }
});

let remembered = null;
try { remembered = localStorage.getItem(SPOT_KEY); } catch (e) { /* fine */ }
setSpotlight(CAMS.includes(remembered) ? remembered : 'chase');

mountCamera('cam-chase', 'fb-chase', '/chase/image');
mountCamera('cam-drone', 'fb-drone', '/airsim_node/PX4/front_center_Scene/image');
mountCamera('cam-depth', 'fb-depth', '/depth_view/image');

// THE LEGEND IS DRAWN FROM THE NODE, not hard-coded. depth_view publishes [near, far] latched
// on /depth_view/range, so a scale can never mislabel a colour map launched with a different
// far_m -- a legend that is confidently wrong is worse than none.
subscribe('/depth_view/range', 'std_msgs/msg/Float32MultiArray', (m) => {
  const [near, far] = m.data || [];
  if (!isFinite(near) || !isFinite(far) || far <= near) return;
  const marks = el('depth-marks');
  const n = marks.children.length;
  for (let i = 0; i < n; i++) {
    marks.children[i].textContent = (near + (far - near) * i / (n - 1)).toFixed(1);
  }
  marks.lastElementChild.textContent += ' m';
});

/* ------------------------------------------------------------------- telemetry */

function subscribe(name, messageType, handler) {
  const topic = new ROSLIB.Topic({
    ros, name, messageType,
    throttle_rate: THROTTLE_MS,
    queue_length: 1,     // newest wins; a queue of stale telemetry is worse than none
  });
  topic.subscribe((msg) => { try { handler(msg); } catch (e) { console.error(name, e); } });
  return topic;
}

subscribe('/fmu/out/vehicle_local_position', 'px4_msgs/msg/VehicleLocalPosition', (m) => {
  // z is NED and DOWN-POSITIVE. This is the single derived value on the page, and it is
  // labelled; conventions §3 keeps the real NED->ENU conversion in control/frames.py alone.
  // Straight through put(), so a missing z gets the em dash AND the stale style,
  // rather than an em dash that looks like a live reading.
  put('t-alt', fmt(-m.z, 2));
  put('t-x', fmt(m.x, 2, ' m'));
  put('t-y', fmt(m.y, 2, ' m'));
  put('t-z', fmt(m.z, 2, ' m'));
  put('t-hdg', fmt(m.heading * 180 / Math.PI, 1, '°'));
  put('t-valid', `xy ${m.xy_valid ? 'ok' : 'NO'} · z ${m.z_valid ? 'ok' : 'NO'}`);

  const ground = Math.hypot(m.vx, m.vy);
  put('t-gspd', fmt(ground, 2, ' m/s'));
  put('t-vspd', fmt(-m.vz, 2, ' m/s'));      // + = climbing, same sign convention as t-alt
  put('t-vel', join([fmt(m.vx, 1), fmt(m.vy, 1), fmt(m.vz, 1)]));
});

subscribe('/fmu/out/vehicle_global_position', 'px4_msgs/msg/VehicleGlobalPosition', (m) => {
  put('t-lat', fmt(m.lat, 7, '°'));
  put('t-lon', fmt(m.lon, 7, '°'));
  put('t-amsl', fmt(m.alt, 2, ' m'));
});

// GPS FIX QUALITY comes from the raw sensor, not the fused global position — a fused
// solution keeps reporting a position after the fix degrades, which is exactly the condition
// a site survey needs to see.
const FIX = { 0: 'none', 1: 'none', 2: '2D', 3: '3D', 4: 'RTCM', 5: 'RTK float', 6: 'RTK fixed' };
// THE TOPIC IS `vehicle_gps_position`, NOT `sensor_gps`. `SensorGps` is the message TYPE;
// PX4 v1.16's uXRCE-DDS bridge advertises it under `/fmu/out/vehicle_gps_position`. The first
// cut used the type name as the topic name, and the page simply showed em dashes for fix and
// satellites -- which is the designed behaviour for a field that never arrives, and is how
// this was caught rather than shipped. Verified against `ros2 topic list` on a live stack.
subscribe('/fmu/out/vehicle_gps_position', 'px4_msgs/msg/SensorGps', (m) => {
  put('t-fix', `${FIX[m.fix_type] ?? m.fix_type} (${m.fix_type})`);
  put('t-sats', m.satellites_used ?? null);
  // The raw sensor carries its own position, so show it rather than the fused one when the
  // two disagree -- a fused solution keeps reporting after the fix degrades, which is exactly
  // what a site survey needs to be able to see.
  put('t-eph', fmt(m.eph, 2, ' m'));
});

subscribe('/fmu/out/vehicle_attitude', 'px4_msgs/msg/VehicleAttitude', (m) => {
  // PX4 orders the quaternion w,x,y,z — NOT the x,y,z,w that ROS geometry_msgs uses. Getting
  // this backwards yields angles that look plausible and are wrong, which is the worst kind.
  const [w, x, y, z] = m.q;
  const roll = Math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y));
  const sinp = 2 * (w * y - z * x);
  const pitch = Math.abs(sinp) >= 1 ? Math.sign(sinp) * Math.PI / 2 : Math.asin(sinp);
  const yaw = Math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z));
  const deg = (r) => fmt(r * 180 / Math.PI, 1, '°');
  put('t-roll', deg(roll));
  put('t-pitch', deg(pitch));
  put('t-yaw', deg(yaw));
});

subscribe('/fmu/out/sensor_combined', 'px4_msgs/msg/SensorCombined', (m) => {
  const g = m.gyro_rad, a = m.accelerometer_m_s2;
  put('t-gyro', g ? join(g.map((v) => fmt(v, 2))) : null);
  put('t-accel', a ? join(a.map((v) => fmt(v, 2))) : null);
});

const NAV = {
  0: 'MANUAL', 2: 'POSCTL', 3: 'AUTO.MISSION', 4: 'AUTO.LOITER', 5: 'AUTO.RTL',
  14: 'OFFBOARD', 17: 'AUTO.TAKEOFF', 18: 'AUTO.LAND',
};
subscribe('/fmu/out/vehicle_status_v1', 'px4_msgs/msg/VehicleStatus', (m) => {
  put('t-armed', m.arming_state === 2 ? 'ARMED' : 'disarmed');
  put('t-nav', `${NAV[m.nav_state] ?? 'state ' + m.nav_state} (${m.nav_state})`);
});

subscribe('/fmu/out/vehicle_land_detected', 'px4_msgs/msg/VehicleLandDetected', (m) => {
  put('t-landed', m.landed ? 'on ground' : (m.ground_contact ? 'ground contact' : 'in air'));
});

subscribe('/fmu/out/battery_status', 'px4_msgs/msg/BatteryStatus', (m) => {
  const pct = (m.remaining === undefined || m.remaining < 0) ? null : fmt(m.remaining * 100, 0, '%');
  const v = fmt(m.voltage_v, 1, ' V');
  put('t-batt', pct && v ? `${pct} · ${v}` : (pct ?? v));
});

/* ------------------------------------------------------------------- the buttons */

// Mirrors control/manual_policy.py. The authoritative copy is in the node — a command
// refused there is logged and dropped, and the reason reaches this page over /rosout. This
// table only decides which buttons look pressable, so drifting from the node makes a button
// look wrong, never makes the aircraft do the wrong thing.
const ALLOWED = {
  takeoff: new Set(['idle']),
  land: new Set(['hover', 'takeoff', 'stream_setpoints', 'request_offboard', 'arm']),
  hold: new Set(['hover']),
  move: new Set(['hover']),
};
const STATE_NAMES = {
  0: 'wait_for_fcu', 1: 'stream_setpoints', 2: 'request_offboard', 3: 'arm', 4: 'takeoff',
  5: 'waypoints', 6: 'land', 7: 'done', 8: 'failed', 9: 'idle', 10: 'hover',
};

let flightState = null;

const commandTopic = new ROSLIB.Topic({
  ros, name: '/mission/command', messageType: 'drone_interfaces/msg/MissionCommand',
});

function send(command) {
  const alt = parseFloat(el('alt').value);
  commandTopic.publish(new ROSLIB.Message({
    header: { stamp: { sec: 0, nanosec: 0 }, frame_id: 'map' },
    command,
    // 0 means "use the node's takeoff_altitude parameter" (MissionCommand.msg), which is
    // also the right answer for a blank or nonsensical box.
    altitude_m: (command === 'takeoff' && isFinite(alt) && alt > 0) ? alt : 0.0,
  }));
  say(`sent ${command}${command === 'takeoff' ? ` to ${alt} m` : ''}`);
}

el('btn-takeoff').onclick = () => {
  // The one confirmation on the page, and only on the command that arms. LAND and HOLD
  // reduce energy; putting a dialog in front of LAND would be actively dangerous.
  if (window.confirm(`Arm and take off to ${el('alt').value} m?`)) send('takeoff');
};
el('btn-land').onclick = () => send('land');
el('btn-hold').onclick = () => send('hold');

function refreshButtons() {
  for (const cmd of ['takeoff', 'land', 'hold']) {
    el('btn-' + cmd).disabled = !connected || !ALLOWED[cmd].has(flightState);
  }
  // The whole movement block greys out unless the aircraft is holding station. MOVE is
  // accepted from HOVER only -- this just makes that visible rather than letting an operator
  // press into a refusal.
  el('mover').classList.toggle('off', !connected || !ALLOWED.move.has(flightState));
}

/* ------------------------------------------------------------------- movement (SIM-47) */

// DEGREES PER NUDGE is fixed while metres are on a slider, because the two are judged
// differently: distance is read off the world, and a yaw step is a comfort setting nobody
// re-tunes mid-survey.
const YAW_STEP_DEG = 15;

function stepSize() {
  const v = parseFloat(el('step').value);
  return isFinite(v) && v > 0 ? v : 2.0;
}

function nudge(d) {
  if (!connected || !ALLOWED.move.has(flightState)) return;
  const m = stepSize();
  commandTopic.publish(new ROSLIB.Message({
    header: { stamp: { sec: 0, nanosec: 0 }, frame_id: 'base_link' },
    command: 'move',
    altitude_m: 0.0,
    // Body frame, FLU -- forward / left / up. The node rotates this into ENU with the vehicle's
    // current heading, in frames.flu_to_enu, which is the one place that rotation happens.
    forward_m: (d.fwd || 0) * m,
    left_m: (d.left || 0) * m,
    up_m: (d.up || 0) * m,
    yaw_delta_rad: (d.yaw || 0) * YAW_STEP_DEG * Math.PI / 180,
  }));
}

for (const b of document.querySelectorAll('.nudge')) {
  b.addEventListener('click', () => nudge({
    fwd: Number(b.dataset.fwd || 0), left: Number(b.dataset.left || 0),
    up: Number(b.dataset.up || 0), yaw: Number(b.dataset.yaw || 0),
  }));
}

const KEYS = {
  w: {fwd: 1}, s: {fwd: -1}, a: {left: 1}, d: {left: -1},
  r: {up: 1},  f: {up: -1},  q: {yaw: 1},  e: {yaw: -1},
};
window.addEventListener('keydown', (ev) => {
  // Ignore keys typed into the altitude or step inputs -- otherwise setting a take-off
  // altitude of 25 would fly the aircraft sideways on its way through the digits.
  if (ev.target.tagName === 'INPUT' || ev.metaKey || ev.ctrlKey || ev.altKey) return;
  const d = KEYS[ev.key.toLowerCase()];
  if (!d) return;
  ev.preventDefault();
  nudge(d);
});

el('step').addEventListener('input', () => {
  el('steplabel').textContent = `${stepSize().toFixed(1)} m · ${YAW_STEP_DEG}°`;
  el('stepnote').textContent = `${stepSize().toFixed(1)} m`;
});

subscribe('/mission/status', 'drone_interfaces/msg/MissionStatus', (m) => {
  flightState = STATE_NAMES[m.state] ?? String(m.state);
  el('state').textContent = flightState.replace(/_/g, ' ');
  el('state-detail').textContent = m.failure_reason
    || (m.distance_to_target_m >= 0 ? `${m.distance_to_target_m.toFixed(2)} m to target` : '');
  refreshButtons();
});

subscribe('/mission/result', 'drone_interfaces/msg/MissionResult', (m) => {
  say(`sortie ${m.outcome}${m.failure_reason ? ': ' + m.failure_reason : ''}`,
      m.outcome !== 'success');
});

// The node logs every refusal with its reason. Surfacing them here is the difference between
// "the button did nothing" and "the SITL interlock is not satisfied".
subscribe('/rosout', 'rcl_interfaces/msg/Log', (m) => {
  if (m.level >= 30 && /REFUSED|refused|interlock|ignoring unknown command|move clamped/.test(m.msg)) {
    say(`${m.name}: ${m.msg}`, true);
  }
});

// If /mission/status never arrives, the controller is not in manual mode (or is not running)
// and every button would sit disabled with no explanation.
setTimeout(() => {
  if (flightState === null && connected) {
    say('no /mission/status after 5 s — is offboard_control running with manual:=true? '
        + 'See scripts/web_ui.sh', true);
  }
}, 5000);
