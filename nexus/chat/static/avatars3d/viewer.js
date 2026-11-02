import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';
import { applyCharacterDesign } from './design/character_design.js?v=005d68g3';

const embedded = new URLSearchParams(location.search).has('embedded');
const originalView = new URLSearchParams(location.search).get('original') === '1';
const PARTNERS = ['hermes', 'nexus', 'gpt'];
const EXPRESSIONS = ['aa', 'ih', 'ou', 'ee', 'oh', 'blink', 'blinkLeft', 'blinkRight', 'happy', 'angry', 'sad', 'relaxed'];
const MOUTHS = ['aa', 'ih', 'ou', 'ee', 'oh'];
const VISEMES = {AI:'aa', E:'ee', O:'oh', U:'ou', FV:'ih'};
const records = new Map();
const stage = document.getElementById('stage');
const canvas = document.getElementById('vrmCanvas');
const loadStatus = document.getElementById('loadStatus');
const metrics = {started:performance.now(), frames:0, frameTimes:[], renderTimes:[], errors:[], loads:{}};
let renderer, status = 'loading', autoMotion = false, view = embedded || !originalView ? 'portrait' : 'full';
let layout = null, visible = !embedded, previousTime = 0, lastFrameTime = 0;
let currentTarget = 'nexus', previousExpression = 'aa';
let resolveReady, rejectReady;
const ready = new Promise((resolve, reject) => { resolveReady = resolve; rejectReady = reject; });
// Readiness is also available as a state for consumers which do not await it.
ready.catch(() => {});

function notify(state, error) {
  if (embedded) parent.postMessage({type:'nexus:vrm-status', status:state, error:error || null}, location.origin);
}
function fail(error) {
  const message = String(error?.message || error);
  if (!metrics.errors.includes(message)) metrics.errors.push(message);
  status = 'error';
  loadStatus.textContent = '3D nicht verfügbar: ' + message;
  loadStatus.classList.add('error');
  notify('error', message);
  rejectReady(new Error(message));
}
function getRecord(partner) {
  const record = records.get(partner);
  if (!record) throw new Error('Modell noch nicht verfügbar: ' + partner);
  return record;
}
function setExpression(partner, name, value) {
  const record = getRecord(partner);
  if (!EXPRESSIONS.includes(name) || !record.vrm.expressionManager.getExpression(name)) throw new Error('Expression fehlt: ' + name);
  const weight = Math.min(1, Math.max(0, Number(value) || 0));
  record.expressions[name] = weight;
  record.vrm.expressionManager.setValue(name, weight);
  return weight;
}
function rotation(record, boneName, angles, limit) {
  const bone = record.vrm.humanoid.getNormalizedBoneNode(boneName);
  if (!bone) throw new Error('Knochen fehlt: ' + boneName);
  const clamp = value => THREE.MathUtils.clamp(Number(value) || 0, -limit, limit);
  const euler = new THREE.Euler(clamp(angles.pitch), clamp(angles.yaw), clamp(angles.roll), 'YXZ');
  bone.quaternion.copy(record.rest[boneName]).multiply(new THREE.Quaternion().setFromEuler(euler));
}
function setHead(partner, angles) { rotation(getRecord(partner), 'head', angles || {}, Math.PI / 4); }
function setEyes(partner, angles) {
  const record = getRecord(partner);
  rotation(record, 'leftEye', angles || {}, Math.PI / 9);
  rotation(record, 'rightEye', angles || {}, Math.PI / 9);
}
function reset(partner) {
  for (const id of partner ? [partner] : PARTNERS) {
    const record = records.get(id);
    if (!record) continue;
    for (const name of EXPRESSIONS) setExpression(id, name, 0);
    for (const name of ['head', 'leftEye', 'rightEye']) {
      record.vrm.humanoid.getNormalizedBoneNode(name)?.quaternion.copy(record.rest[name]);
    }
    record.vrm.springBoneManager?.reset();
  }
}
function setAutoMotion(value) {
  autoMotion = value === true;
  for (const record of records.values()) record.vrm.springBoneManager?.reset();
  if (!autoMotion) for (const id of records.keys()) setExpression(id, 'blink', 0);
}
function rectFor(partner) {
  if (embedded && layout?.[partner]) return layout[partner];
  const element = document.querySelector('[data-partner="' + partner + '"] .viewport');
  const a = element.getBoundingClientRect(), b = stage.getBoundingClientRect();
  return {x:a.x - b.x, y:a.y - b.y, width:a.width, height:a.height};
}
function fitCamera(record, rect) {
  const {camera, bounds} = record;
  const aspect = rect.width / Math.max(1, rect.height);
  const center = bounds.getCenter(new THREE.Vector3()), size = bounds.getSize(new THREE.Vector3());
  let height;
  if (view === 'portrait' && record.design) {
    // Include the actual animated head costume (wings, cups, hood and ponytail)
    // and upper chest. Fit both dimensions, including narrow mobile slots.
    const head = record.vrm.humanoid.getRawBoneNode('head').getWorldPosition(new THREE.Vector3());
    const frame = new THREE.Box3().setFromObject(record.design.root);
    const m = record.design.details.measurements;
    frame.expandByPoint(new THREE.Vector3(head.x - .265, head.y - .38, head.z));
    frame.expandByPoint(new THREE.Vector3(head.x + .265, head.y + m.hairTopY, head.z));
    frame.getCenter(center);
    const span = frame.getSize(new THREE.Vector3());
    height = Math.max(span.y, span.x / aspect) * 1.12;
  } else if (view === 'portrait') {
    const head = record.vrm.humanoid.getRawBoneNode('head');
    head.getWorldPosition(center);
    center.y -= originalView ? .055 : .025;
    height = .63;
  } else {
    // The posed body bounds are stable; refresh rigid head accessories as the
    // head moves so their tips remain inside the full-body camera too.
    const full = bounds.clone();
    if (record.design) full.union(new THREE.Box3().setFromObject(record.design.root));
    full.getCenter(center);full.getSize(size);
    height = Math.max(size.y, size.x / aspect) * 1.12;
  }
  camera.left = -height * aspect / 2; camera.right = height * aspect / 2;
  camera.top = height / 2; camera.bottom = -height / 2;
  camera.position.set(center.x, center.y, center.z + 4);
  camera.lookAt(center);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld(true);
}
function render(delta = 0, time = performance.now()) {
  if (!renderer || !stage.clientWidth || !stage.clientHeight) return;
  const width = stage.clientWidth, height = stage.clientHeight;
  const dpr = Math.min(devicePixelRatio || 1, 1.5);
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
    renderer.setPixelRatio(dpr);
    renderer.setSize(width, height, false);
  }
  renderer.info.reset();
  renderer.setScissorTest(false);
  renderer.setClearColor(0x07101a, embedded ? 0 : 1);
  renderer.clear();
  renderer.setScissorTest(true);
  let i = 0;
  for (const partner of PARTNERS) {
    const record = records.get(partner);
    if (!record) { i++; continue; }
    if (autoMotion) {
      const cycle = ((time / 1000 + i * 1.31) % (5.2 + i * .45));
      setExpression(partner, 'blink', cycle < .2 ? Math.sin(cycle / .2 * Math.PI) : 0);
    }
    // A zero delta freezes springs during deterministic manual verification.
    record.vrm.update(autoMotion ? Math.min(delta, .05) : 0);
    record.design?.update(record.expressions);
    record.vrm.scene.updateMatrixWorld(true);
    const rect = rectFor(partner);
    if (rect.width > 0 && rect.height > 0) {
      fitCamera(record, rect);
      const x = Math.max(0, rect.x), y = Math.max(0, height - rect.y - rect.height);
      renderer.setViewport(x, y, rect.width, rect.height);
      renderer.setScissor(x, y, rect.width, rect.height);
      renderer.render(record.scene, record.camera);
      record.viewport = {...rect};
    }
    i++;
  }
  renderer.setScissorTest(false);
}
function animate(time) {
  requestAnimationFrame(animate);
  if (!visible || document.hidden || status === 'error') { previousTime = 0; lastFrameTime = 0; return; }
  const delta = previousTime ? (time - previousTime) / 1000 : 0;
  previousTime = time;
  if (lastFrameTime) {
    metrics.frameTimes.push(time - lastFrameTime);
    if (metrics.frameTimes.length > 600) metrics.frameTimes.shift();
  }
  lastFrameTime = time;
  const start = performance.now();
  try { render(delta, time); } catch (error) { fail(error); return; }
  metrics.renderTimes.push(performance.now() - start);
  if (metrics.renderTimes.length > 600) metrics.renderTimes.shift();
  metrics.frames++;
}
function inspect(partner) {
  const record = getRecord(partner), meshes = [];
  record.vrm.scene.traverse(object => {
    if (object.isMesh) meshes.push({uuid:object.uuid, name:object.name, geometryUuid:object.geometry.uuid,
      morphTargetInfluences:object.morphTargetInfluences ? [...object.morphTargetInfluences] : []});
  });
  const bone = name => record.vrm.humanoid.getNormalizedBoneNode(name)?.quaternion.toArray() || null;
  const raw = name => record.vrm.humanoid.getRawBoneNode(name)?.quaternion.toArray() || null;
  return {partner, modelUuid:record.vrm.scene.uuid, sceneUuid:record.scene.uuid, meshes,
    expressions:Object.fromEntries(EXPRESSIONS.map(name => [name, record.vrm.expressionManager.getValue(name)])),
    head:bone('head'), eyes:{left:bone('leftEye'), right:bone('rightEye')},
    rawHead:raw('head'), rawEyes:{left:raw('leftEye'), right:raw('rightEye')},
    viewport:record.viewport || rectFor(partner), bounds:{min:record.bounds.min.toArray(), max:record.bounds.max.toArray()},
    camera:{left:record.camera.left,right:record.camera.right,top:record.camera.top,bottom:record.camera.bottom,
      near:record.camera.near,far:record.camera.far,position:record.camera.position.toArray(),quaternion:record.camera.quaternion.toArray(),
      matrixWorld:record.camera.matrixWorld.toArray(),projectionMatrix:record.camera.projectionMatrix.toArray()},
    original:record.title, url:record.url, loadMs:metrics.loads[partner], design:record.design?.details || null,
    expressionBindings:Object.fromEntries(EXPRESSIONS.map(name => [name, record.vrm.expressionManager.getExpression(name)?.binds.length || 0]))};
}
function getStats() {
  const gl = renderer?.getContext(), extension = gl?.getExtension('WEBGL_debug_renderer_info');
  const gpu = extension ? gl.getParameter(extension.UNMASKED_RENDERER_WEBGL) : gl?.getParameter(gl.RENDERER);
  const values = [...metrics.frameTimes].sort((a,b) => a-b);
  const average = metrics.frameTimes.reduce((sum,value) => sum+value,0) / Math.max(1, metrics.frameTimes.length);
  const percentile = q => values.length ? values[Math.floor((values.length-1)*q)] : null;
  return {status, versions:{three:THREE.REVISION, threeVrm:'3.5.5'}, gpu,
    softwareRenderer:/swiftshader|llvmpipe|software|basic render/i.test(gpu || ''),
    frames:metrics.frames, frameSamples:values.length, fps:average ? 1000/average : null,
    frameMsP50:percentile(.5), frameMsP95:percentile(.95),
    renderMsAverage:metrics.renderTimes.reduce((sum,value) => sum+value,0) / Math.max(1,metrics.renderTimes.length),
    drawCalls:renderer?.info.render.calls, triangles:renderer?.info.render.triangles,
    geometries:renderer?.info.memory.geometries, textures:renderer?.info.memory.textures,
    canvas:{width:canvas.width,height:canvas.height}, loads:{...metrics.loads},
    totalLoadMs:metrics.totalLoadMs || null, errors:[...metrics.errors]};
}
function snapshot() { return {status, versions:{three:THREE.REVISION,threeVrm:'3.5.5'}, view, autoMotion, originalView,
  models:Object.fromEntries([...records.keys()].map(id => [id,inspect(id)])), stats:getStats()}; }
function renderOnce() { render(0); return Promise.resolve(snapshot()); }
function setView(value) { if (!['full','portrait'].includes(value)) return; view = value; render(0); }
window.NEXUS_VRM = Object.freeze({ready, get status(){return status;}, snapshot, inspect,
  getModel:partner => getRecord(partner).vrm, setExpression, setHead, setEyes, reset,
  getDesign:partner => getRecord(partner).design || null,
  setAutoMotion, setView, renderOnce, getStats});

async function load(partner) {
  const started = performance.now();
  const url = '/static/assets/avatars/vrm-005d68e/' + partner + '.vrm';
  const loader = new GLTFLoader();
  loader.register(parser => new VRMLoaderPlugin(parser));
  const gltf = await loader.loadAsync(url);
  const vrm = gltf.userData.vrm;
  if (!vrm || vrm.meta?.licenseName !== 'CC0') throw new Error(partner + ': CC0-VRM-Metadaten fehlen');
  VRMUtils.rotateVRM0(vrm);
  if (vrm.lookAt) vrm.lookAt.autoUpdate = false;
  const rest = {};
  for (const name of ['head','leftEye','rightEye']) {
    const node = vrm.humanoid.getNormalizedBoneNode(name);
    if (!node) throw new Error(partner + ': ' + name + ' fehlt');
    rest[name] = node.quaternion.clone();
  }
  const scene = new THREE.Scene();
  scene.add(vrm.scene);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x304860, originalView ? 2.4 : 1.8));
  const key = new THREE.DirectionalLight(0xffffff, originalView ? 3.2 : 2.6);
  key.position.set(-1, 2, 3); scene.add(key);
  const fill = new THREE.DirectionalLight(0xb6dfff, .8);
  fill.position.set(2, 1, -2); scene.add(fill);
  const rim = new THREE.DirectionalLight(0x37aaff, 1.3);
  rim.position.set(0, 2, -3); if (!originalView) scene.add(rim);
  vrm.scene.updateMatrixWorld(true);
  const design = originalView ? null : applyCharacterDesign(vrm,partner,scene);
  const bounds = new THREE.Box3().setFromObject(vrm.scene);
  const record = {vrm, scene, camera:new THREE.OrthographicCamera(-1,1,1,-1,.01,100),
    rest, bounds, design, expressions:{}, url, title:vrm.meta.name || vrm.meta.title || partner};
  records.set(partner, record);
  for (const name of EXPRESSIONS) {
    const expression = vrm.expressionManager.getExpression(name);
    if (!expression || !expression.binds.length) throw new Error(partner + ': wirksames Binding fehlt: ' + name);
    setExpression(partner,name,0);
  }
  metrics.loads[partner] = performance.now() - started;
  document.querySelector('[data-partner="' + partner + '"] .model-status').textContent = originalView ? 'Original geladen · separat steuerbar' : 'Charakter geladen · separat steuerbar';
  if (!originalView) document.querySelector('[data-partner="' + partner + '"] .model-card-note, [data-partner="' + partner + '"] p').textContent =
    {nexus:'Kapuze · Silberpanzer · blaues Visier',hermes:'Silberhaar · Flügelheadset · Android',gpt:'Silberblond · Leuchtheadset · Android'}[partner];
}
function controls() {
  const q = id => document.getElementById(id);
  const clear = () => {
    for (const id of ['weight','headYaw','headPitch','eyeYaw','eyePitch']) q(id).value = 0;
    q('weightValue').textContent = '0';
  };
  q('target').addEventListener('change', () => {
    currentTarget=q('target').value;
    if (status!=='ready') {clear();return;}
    const record=getRecord(currentTarget);
    const weight=record.vrm.expressionManager.getValue(q('expression').value)||0;
    q('weight').value=weight;q('weightValue').textContent=String(weight);
    const angles=name=>new THREE.Euler().setFromQuaternion(record.rest[name].clone().invert().multiply(record.vrm.humanoid.getNormalizedBoneNode(name).quaternion),'YXZ');
    const head=angles('head'),eye=angles('leftEye');
    q('headYaw').value=THREE.MathUtils.radToDeg(head.y);q('headPitch').value=THREE.MathUtils.radToDeg(head.x);
    q('eyeYaw').value=THREE.MathUtils.radToDeg(eye.y);q('eyePitch').value=THREE.MathUtils.radToDeg(eye.x);
  });
  q('expression').addEventListener('change', () => {
    if (status !== 'ready') return;
    setExpression(currentTarget,previousExpression,0); previousExpression=q('expression').value;
    q('weight').value=0; q('weightValue').textContent='0';
  });
  q('weight').addEventListener('input', () => {
    if (status !== 'ready') return;
    try { setExpression(currentTarget,q('expression').value,q('weight').value); q('weightValue').textContent=q('weight').value; }
    catch(error) { q('controlStatus').textContent=error.message; }
  });
  for (const id of ['headYaw','headPitch']) q(id).addEventListener('input', () => {
    if (status==='ready') setHead(currentTarget,{yaw:THREE.MathUtils.degToRad(q('headYaw').value),pitch:THREE.MathUtils.degToRad(q('headPitch').value)});
  });
  for (const id of ['eyeYaw','eyePitch']) q(id).addEventListener('input', () => {
    if (status==='ready') setEyes(currentTarget,{yaw:THREE.MathUtils.degToRad(q('eyeYaw').value),pitch:THREE.MathUtils.degToRad(q('eyePitch').value)});
  });
  q('reset').addEventListener('click', () => {reset(currentTarget);clear();});
  q('autoMotion').addEventListener('change', () => setAutoMotion(q('autoMotion').checked));
  q('viewMode').addEventListener('click', () => {setView(view==='full'?'portrait':'full');q('viewMode').textContent=view==='full'?'Porträtansicht':'Ganzkörperansicht';});
}
window.addEventListener('message', event => {
  if (!embedded || event.source !== parent || event.origin !== location.origin) return;
  const message = event.data || {};
  if (message.type === 'nexus:vrm-layout' && message.slots) layout = message.slots;
  if (message.type === 'nexus:vrm-visible') {visible=message.visible===true; if(!visible)reset();}
  if (message.type === 'nexus:vrm-motion' && status==='ready') {
    for (const partner of PARTNERS) {
      const state = message.partners?.[partner];
      if (!state) continue;
      for (const name of MOUTHS) setExpression(partner,name,0);
      const expression = VISEMES[state.viseme];
      if (state.speaking && expression) setExpression(partner,expression,state.weight);
      setExpression(partner,'blink',state.blink);
      setHead(partner,{roll:THREE.MathUtils.degToRad(Number(state.headRoll)||0)});
      setEyes(partner,{yaw:(Number(state.eyeX)||0)*.055,pitch:(Number(state.eyeY)||0)*.055});
    }
  }
});
try {
  renderer = new THREE.WebGLRenderer({canvas,alpha:true,antialias:true,powerPreference:'high-performance'});
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1;
  renderer.info.autoReset = false;
  canvas.addEventListener('webglcontextlost', event => {event.preventDefault();fail('WebGL-Kontext verloren');});
  controls();
  requestAnimationFrame(animate);
  await Promise.all(PARTNERS.map(load));
  reset();
  status = 'ready'; metrics.totalLoadMs = performance.now() - metrics.started;
  render(0);
  loadStatus.textContent = originalView ? 'Alle drei Originalmodelle geladen · jede Figur unabhängig steuerbar' : 'Alle drei Charaktere geladen · jede Figur unabhängig steuerbar';
  if (originalView) {
    document.querySelector('.note').textContent='Unveränderte CC0-Originalmodelle zum Vergleich.';
    document.getElementById('designComparison').href='index.html';
    document.getElementById('designComparison').textContent='Charakterdesign anzeigen';
    document.getElementById('viewMode').textContent='Porträtansicht';
  }
  document.getElementById('controlStatus').textContent = 'Figur auswählen, Mundform oder Mimik und Stärke einstellen. Kopf und Augen getrennt steuern.';
  resolveReady(snapshot()); notify('ready');
} catch (error) { fail(error); }
