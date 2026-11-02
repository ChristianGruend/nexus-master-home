/* NEXUS reference character: original VRM rig, reversible geometric costume.
 * All surfaces are authored here; no downloaded textures or model replacements.
 */
export function buildNexus({THREE, vrm, rig, materials = {}}) {
  const root = new THREE.Group();
  root.name = 'NEXUS_reference_hood_and_android_armour';
  const m = rig.measurements || {};
  const width = m.headWidth || .223735;
  const height = m.headHeight || .244285;
  const scale = width / .17;
  const verticalScale = height / .23;
  const top = m.headTopY ?? .195067;
  const chin = m.chinY ?? -.049218;
  const eye = m.eyeY ?? .062553;
  const face = m.faceZ ?? .121393;
  const originY = chin;
  const p = (x,y,z) => new THREE.Vector3(x * scale, originY + y * verticalScale, face + (z - .119) * scale);
  const own = (name, color, metalness, roughness, extras = {}) => {
    const material = new THREE.MeshStandardMaterial({color, metalness, roughness, ...extras});
    material.name = 'NEXUS_' + name;
    return material;
  };
  const silver = materials.silver || own('brushed_silver',0xb7cbdc,.87,.28);
  const dark = materials.dark || own('gunmetal',0x12202e,.88,.35);
  const black = materials.black || own('black_ceramic',0x040a10,.6,.26);
  const glow = materials.glowBlue || own('cyan_light',0x61d7ff,.4,.17,{emissive:0x008cff,emissiveIntensity:2.8});
  const cloth = materials.cloth || own('woven_hood',0x080b10,.08,.9,{side:THREE.DoubleSide});
  const lining = own('hood_inner_lining',0x11151d,.02,.96,{side:THREE.DoubleSide});
  const seam = own('hood_stitch',0x3b4857,.18,.7);
  const lensMaterial = own('blue_smoked_visor',0x031d36,.48,.12,{transparent:true,opacity:.62,depthWrite:false,side:THREE.DoubleSide});
  const meshes = [];
  function mesh(name, geometry, material, parent = root) {
    const object = new THREE.Mesh(geometry, material);
    object.name = 'NEXUS_' + name;
    object.castShadow = true;
    object.receiveShadow = true;
    parent.add(object); meshes.push(object);
    return object;
  }
  function panel(name, points, material, parent = root, depth = .002) {
    const vertices = points.map(v => p(...v));
    const signedArea=vertices.reduce((sum,a,i)=>{
      const b=vertices[(i+1)%vertices.length];return sum+a.x*b.y-b.x*a.y;
    },0);
    if(signedArea<0) vertices.reverse();
    const center = vertices.reduce((a,v)=>a.add(v),new THREE.Vector3()).divideScalar(vertices.length);
    const positions = [];
    const put = v => positions.push(v.x,v.y,v.z);
    for(let i = 0; i < vertices.length; i++) {
      const a = vertices[i], b = vertices[(i+1)%vertices.length];
      put(center); put(a); put(b);
      if (depth) {
        const aa = a.clone().add(new THREE.Vector3(0,0,-depth*scale));
        const bb = b.clone().add(new THREE.Vector3(0,0,-depth*scale));
        put(a); put(aa); put(b); put(b); put(aa); put(bb);
      }
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));
    const uvs=[];
    for(let i=0;i<positions.length;i+=3)uvs.push((positions[i]/scale+.22)/.44,(positions[i+1]-originY)/verticalScale+.2);
    geometry.setAttribute('uv',new THREE.Float32BufferAttribute(uvs,2));
    geometry.computeVertexNormals();
    const object = mesh(name, geometry, material, parent);
    // Plate outlines deliberately read as separate machined components.
    return object;
  }
  function line(name, points, radius, material, parent = root) {
    const curve = new THREE.CatmullRomCurve3(points.map(v=>p(...v)));
    return mesh(name,new THREE.TubeGeometry(curve,Math.max(8,points.length*6),radius*scale,5,false),material,parent);
  }
  function cylinderBetween(name,a,b,radius,material,parent=root) {
    const start=p(...a), end=p(...b), length=start.distanceTo(end);
    const object=mesh(name,new THREE.CylinderGeometry(radius*scale,radius*scale,length,12),material,parent);
    object.position.copy(start).add(end).multiplyScalar(.5);
    object.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),end.sub(start).normalize());
    return object;
  }

  // A folded cowl, open at the face. Eight depth bands form a real curved shell;
  // the pointed opening and weighted folds are not a flat cut-out silhouette.
  const opening = [
    [-.192,-.115,.015],[-.169,-.063,.075],[-.145,.008,.1],[-.127,.087,.105],
    [-.125,.165,.097],[-.092,.235,.075],[-.047,.272,.044],[0,.296,.026],
    [.047,.272,.044],[.092,.235,.075],[.125,.165,.097],[.127,.087,.105],
    [.145,.008,.1],[.169,-.063,.075],[.192,-.115,.015]
  ];
  const positions=[], indices=[], hoodUvs=[], rows=11;
  for(let row=0; row<=rows; row++) {
    const t=row/rows;
    for(let i=0;i<opening.length;i++) {
      const [x,y,z]=opening[i];
      const arch=Math.sin(i/(opening.length-1)*Math.PI);
      const fold=Math.sin(i*2.57+t*4.2)*Math.sin(t*Math.PI)*.006;
      const v=p(x*(1-.3*t)+Math.sign(x)*fold,y+.01*Math.sin(t*Math.PI)-.026*t*arch,z*(1-t)-.107*t-.019*Math.sin(t*Math.PI));
      positions.push(v.x,v.y,v.z);
      hoodUvs.push(i/(opening.length-1),t);
      if(row<rows&&i<opening.length-1){const a=row*opening.length+i,b=a+opening.length;indices.push(a,b,a+1,b,b+1,a+1);}
    }
  }
  const hoodGeometry=new THREE.BufferGeometry();
  hoodGeometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));
  hoodGeometry.setAttribute('uv',new THREE.Float32BufferAttribute(hoodUvs,2));
  hoodGeometry.setIndex(indices);hoodGeometry.computeVertexNormals();
  mesh('tailored_folded_hood',hoodGeometry,cloth);
  line('rolled_hood_opening',opening,.0048,lining);
  line('hood_outer_piping',opening.map(([x,y,z])=>[x*1.015,y+.002,z+.002]),.0013,seam);
  for(const side of [-1,1]) {
    line('hood_' + side + '_crown_seam',[[side*.168,-.065,.075],[side*.126,.08,.106],[side*.105,.198,.084],[side*.044,.273,.048],[0,.296,.028]],.0007,seam);
    line('hood_' + side + '_fabric_fold',[[side*.184,-.11,.02],[side*.157,-.049,.058],[side*.142,.015,.062],[side*.133,.081,.033]],.0024,lining);
    panel('cowl_fold_' + side,[[side*.19,-.105,.025],[side*.103,-.058,.118],[side*.065,-.102,.138],[side*.158,-.151,.07]],cloth);
    line('cowl_sewn_edge_' + side,[[side*.19,-.105,.029],[side*.103,-.058,.121],[side*.065,-.102,.141]],.0011,seam);
  }

  // Segmented silver helmet structure. The mouth and both native skinned eyes
  // remain uncovered; the blue lens is deliberately translucent.
  panel('forehead_crown',[[0,.236,.069],[-.061,.215,.084],[-.055,.18,.104],[-.027,.163,.112],[.027,.163,.112],[.055,.18,.104],[.061,.215,.084]],silver);
  panel('forehead_center_inlay',[[-.008,.233,.074],[-.013,.196,.1],[0,.179,.111],[.013,.196,.1],[.008,.233,.074]],dark);
  line('forehead_panel_gap',[[0,.235,.075],[0,.214,.089],[0,.194,.107]],.0008,black);
  line('brow_upper_metal_edge',[[-.075,.178,.096],[-.043,.171,.114],[0,.162,.119],[.043,.171,.114],[.075,.178,.096]],.0032,silver);
  for(const side of [-1,1]) {
    panel('temple_armour_' + side,[[side*.059,.218,.082],[side*.084,.19,.071],[side*.088,.137,.072],[side*.071,.105,.103],[side*.058,.125,.116],[side*.066,.174,.105]],silver);
    panel('cheek_carapace_' + side,[[side*.074,.125,.105],[side*.089,.095,.082],[side*.077,.044,.09],[side*.047,.011,.108],[side*.039,.032,.117],[side*.054,.059,.117]],silver);
    panel('cheek_dark_inlay_' + side,[[side*.081,.098,.088],[side*.07,.06,.103],[side*.049,.033,.116],[side*.046,.043,.119],[side*.064,.073,.108]],dark);
    line('cheek_luminous_trace_' + side,[[side*.075,.117,.108],[side*.072,.093,.11],[side*.06,.075,.119],[side*.052,.057,.123]],.0016,glow);
    line('temple_machine_cut_' + side,[[side*.067,.206,.09],[side*.075,.18,.092],[side*.076,.155,.098]],.00085,black);
    for(let i=0;i<3;i++) panel('temple_vent_' + side + '_' + i,[[side*.081,.15-i*.01,.087],[side*.086,.146-i*.01,.082],[side*.084,.142-i*.01,.084],[side*.079,.145-i*.01,.091]],black);
    const ear=new THREE.Group();ear.name='NEXUS_circular_audio_array_' + side;root.add(ear);
    const disc=mesh('ear_gunmetal_housing_' + side,new THREE.CylinderGeometry(.044*scale,.041*scale,.02*scale,36),dark,ear);
    disc.rotation.z=Math.PI/2;disc.position.copy(p(side*.105,.12,.01));
    const ring=mesh('ear_silver_rim_' + side,new THREE.TorusGeometry(.035*scale,.0042*scale,8,40),silver,ear);
    ring.rotation.y=Math.PI/2;ring.position.copy(p(side*.117,.12,.01));
    const neon=mesh('ear_cyan_halo_' + side,new THREE.TorusGeometry(.029*scale,.0018*scale,6,40),glow,ear);
    neon.rotation.y=Math.PI/2;neon.position.copy(p(side*.122,.12,.01));
    for(let i=0;i<6;i++) {
      const a=i*Math.PI/3;
      const bolt=mesh('ear_fastener_' + side + '_' + i,new THREE.SphereGeometry(.0023*scale,7,5),silver,ear);
      bolt.position.copy(p(side*.123,.12+Math.cos(a)*.037,.01+Math.sin(a)*.037));
    }
    const earPivot=p(side*.105,.12,.01);
    for(const part of ear.children) part.position.sub(earPivot);
    ear.position.copy(earPivot);ear.position.z+=.022*scale;
    ear.rotation.y=-side*THREE.MathUtils.degToRad(25);
  }

  const visorY=(eye-originY)/verticalScale;
  panel('transparent_blue_optical_visor',[[-.079,visorY+.031,.106],[-.038,visorY+.032,.132],[0,visorY+.025,.139],[.038,visorY+.032,.132],[.079,visorY+.031,.106],[.074,visorY-.027,.112],[.038,visorY-.038,.136],[0,visorY-.049,.144],[-.038,visorY-.038,.136],[-.074,visorY-.027,.112]],lensMaterial,root,0);
  // Silver bridge and stepped cheek shields turn the optical region into a
  // readable robot mask while keeping both native irises and the mouth visible.
  panel('central_optical_bridge',[[0,visorY+.031,.141],[-.009,visorY+.012,.145],[-.007,visorY-.022,.15],[0,visorY-.042,.151],[.007,visorY-.022,.15],[.009,visorY+.012,.145]],dark);
  line('bridge_silver_machine_edge',[[0,visorY+.028,.145],[0,visorY+.007,.151],[0,visorY-.018,.155]],.0011,silver);
  for(const side of [-1,1]) {
    panel('inner_cheek_mechanical_shield_' + side,[[side*.073,visorY-.017,.117],[side*.058,visorY-.036,.134],[side*.037,visorY-.056,.139],[side*.029,visorY-.072,.133],[side*.051,visorY-.058,.126],[side*.072,visorY-.033,.117]],silver);
    panel('lower_cheek_ceramic_recess_' + side,[[side*.063,visorY-.037,.128],[side*.043,visorY-.057,.14],[side*.039,visorY-.064,.136],[side*.059,visorY-.052,.129]],black);
    line('inner_cheek_optical_rim_' + side,[[side*.074,visorY+.027,.112],[side*.077,visorY-.012,.117],[side*.064,visorY-.034,.132],[side*.037,visorY-.058,.144]],.0013,glow);
  }
  const eyeBars=[];
  for(const side of [-1,1]) {
    const bar=new THREE.Group();bar.name='NEXUS_' + (side<0?'left':'right') + '_responsive_optical_strip';root.add(bar);
    const lower=visorY-.006;
    const object=panel('angular_cyan_eye_' + side,[[side*.071,visorY+.011,.111],[side*.046,visorY+.004,.13],[side*.014,lower,.132],[side*.027,visorY-.009,.133],[side*.052,visorY-.001,.129]],glow,bar,.0007);
    const pivot=p(side*.043,visorY,.126);
    object.geometry.translate(-pivot.x,-pivot.y,-pivot.z);
    object.position.copy(pivot);
    object.position.z+=.015*scale;
    eyeBars.push({group:bar,object,side,restY:bar.position.y});
    line('visor_silver_frame_' + side,[[side*.082,visorY+.025,.102],[side*.05,visorY+.032,.118],[side*.018,visorY+.023,.128]],.0018,silver);
  }

  const jaw=new THREE.Group();jaw.name='NEXUS_expression_responsive_jaw';root.add(jaw);
  panel('silver_mandible',[[-.043,.025,.114],[-.022,-.002,.12],[0,-.016,.128],[.022,-.002,.12],[.043,.025,.114],[.029,.016,.125],[0,.004,.138],[-.029,.016,.125]],silver,jaw);
  line('jaw_cyan_center',[[0,-.009,.132],[0,.001,.141]],.0012,glow,jaw);
  const mouthLights=[];
  for(let i=0;i<5;i++) {
    const mouth=mesh('mouth_audio_segment_' + i,new THREE.BoxGeometry(.004*scale,.0014*scale,.0012*scale),glow,jaw);
    mouth.position.copy(p((i-2)*.006,.018,.124));mouthLights.push(mouth);
  }

  // Layered neck pistons and raised collar bridge hood and original torso.
  for(const side of [-1,1]) {
    cylinderBetween('neck_hydraulic_' + side,[side*.027,-.013,.044],[side*.039,-.103,.047],.006,dark);
    cylinderBetween('neck_chrome_piston_' + side,[side*.031,-.046,.049],[side*.039,-.108,.052],.0031,silver);
    panel('raised_collar_' + side,[[side*.045,-.055,.057],[side*.077,-.09,.072],[side*.063,-.159,.109],[side*.027,-.13,.117]],silver);
    line('collar_neon_' + side,[[side*.052,-.069,.064],[side*.068,-.095,.079],[side*.055,-.145,.112]],.0014,glow);
  }

  function value(state,name) {
    const values=state?.expressions || state || {};
    return THREE.MathUtils.clamp(Number(values[name] ?? vrm.expressionManager?.getValue(name)) || 0,0,1);
  }
  function update(state={}) {
    const blink=value(state,'blink');
    const open=Math.max(...['aa','ih','ou','ee','oh'].map(name=>value(state,name)));
    const happy=value(state,'happy'), angry=value(state,'angry'), sad=value(state,'sad');
    jaw.position.y=-open*.011*verticalScale;
    jaw.rotation.x=-open*.065;
    const gazeYaw=THREE.MathUtils.clamp(Number(state.gaze?.yaw)||0,-Math.PI/9,Math.PI/9);
    const gazePitch=THREE.MathUtils.clamp(Number(state.gaze?.pitch)||0,-Math.PI/9,Math.PI/9);
    eyeBars.forEach(({group,object,side})=>{
      const individual=value(state,side<0?'blinkLeft':'blinkRight');
      object.scale.y=1-Math.max(blink,individual)*.88;
      object.rotation.z=side*(happy*.035-angry*.09+sad*.045);
      group.position.x=gazeYaw*.032;
      group.position.y=-gazePitch*.025;
    });
    mouthLights.forEach((object,i)=>{object.scale.y=1+open*(2.5-Math.abs(i-2)*.55);});
  }
  update();
  return {root,update,details:{
    id:'nexus',design:'reference_hooded_silver_android',
    features:['pointed folded black hood','segmented machined silver face frame','transparent blue optical visor','independent cyan optical strips','expression responsive mandible','circular luminous ear arrays','neck pistons and raised armour collar'],
    preservation:'Original VRM file, skeleton, morph bindings, native mouth and eye skinning retained; costume is additive and reversible.',
    meshCount:meshes.length,originalGeometryUntouched:true
  }};
}
