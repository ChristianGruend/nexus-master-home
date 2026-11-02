/** Reference-led mechanical details for the two unmasked android characters.
 * Geometry is generated locally; original CC0 VRM files and facial morphs stay intact.
 * The returned group belongs to the raw-head frame supplied by character_design.js.
 */
export function buildAndroid({ THREE, vrm, partner, rig, materials }) {
  const root = new THREE.Group();
  root.name = `${partner}-android-reference-design`;
  if (partner !== 'hermes' && partner !== 'gpt') return { root, update() {}, details: {} };

  const m = rig.measurements;
  const width = m.headWidth || .22;
  const height = m.headHeight || .24;
  const eyeY = Number.isFinite(m.eyeY) ? m.eyeY : .062;
  const faceZ = Number.isFinite(m.faceZ) ? m.faceZ : width * .49;
  const topY = Number.isFinite(m.headTopY) ? m.headTopY : .19;
  const chinY = Number.isFinite(m.chinY) ? m.chinY : -.045;
  const isHermes = partner === 'hermes';
  const silver = materials.silver;
  const dark = materials.dark;
  const black = materials.black || dark;
  const glow = isHermes ? materials.glowBlue : materials.glowViolet;
  const blue = materials.glowBlue;
  const paleMetal = new THREE.MeshStandardMaterial({
    color: isHermes ? 0xcbd9e5 : 0xe2dde1, metalness: .70, roughness: .34,
  });
  const hair = new THREE.MeshStandardMaterial({
    color: 0xc8c1ad, metalness: .36, roughness: .38,
  });
  const hairHighlight = new THREE.MeshStandardMaterial({
    color: 0xe4ddc9, metalness: .42, roughness: .29,
  });
  const details = {
    design: isHermes ? 'silver android with articulated metallic headset wings' : 'female android with silver-blonde high ponytail and violet headset',
    layeredMetalFeathers: 0, earphoneLayers: 0, facialSidePanels: 0,
    hairLocks: 0, facialMorphsUnmodified: true,
    measurements: { width, height, eyeY, faceZ, topY, chinY },
  };
  const luminous = [];
  const earPivots = new Map();

  function mesh(geometry, material, parent, name) {
    const object = new THREE.Mesh(geometry, material);
    object.name = name || 'machined-android-detail';
    object.castShadow = false;
    object.receiveShadow = false;
    parent.add(object);
    return object;
  }

  function tube(points, radius, material, parent, name, segments = 24) {
    const curve = new THREE.CatmullRomCurve3(points.map(p => new THREE.Vector3(...p)));
    return mesh(new THREE.TubeGeometry(curve, segments, radius, 6, false), material, parent, name);
  }

  function cylinder(radius, length, material, parent, x, y, z, name) {
    const object = mesh(new THREE.CylinderGeometry(radius, radius, length, 40, 1), material, parent, name);
    object.rotation.z = Math.PI / 2;
    object.position.set(x, y, z);
    return object;
  }

  function ring(radius, thickness, material, parent, x, y, z, name) {
    const object = mesh(new THREE.TorusGeometry(radius, thickness, 8, 48), material, parent, name);
    object.rotation.y = Math.PI / 2;
    object.position.set(x, y, z);
    if (material === glow || material === blue) luminous.push(object);
    return object;
  }

  function extrude(shape, depth, material, parent, bevel = width * .008, name) {
    const geometry = new THREE.ExtrudeGeometry(shape, {
      depth, steps: 1, bevelEnabled: true,
      bevelSize: bevel, bevelThickness: bevel * .6, bevelSegments: 2, curveSegments: 10,
    });
    return mesh(geometry, material, parent, name);
  }

  function panel(points, z, depth, material, name) {
    const shape = new THREE.Shape();
    points.forEach((p, i) => i ? shape.lineTo(p[0], p[1]) : shape.moveTo(p[0], p[1]));
    shape.closePath();
    const object = extrude(shape, depth, material, root, width * .007, name);
    object.position.z = z;
    return object;
  }

  // The ear assemblies have a recessed acoustic centre, bevelled case, separate
  // lens, two luminous seals and screws. Their axis follows the side of the head.
  const earRadius = height * (isHermes ? .17 : .18);
  const earY = eyeY - height * .035;
  const earZ = faceZ - width * .30;
  const earX = width * .61;
  for (const side of [-1, 1]) {
    const earPivot = new THREE.Group();
    earPivot.name = `${partner}-${side < 0 ? 'left' : 'right'}-forward-earcup-pivot`;
    earPivot.position.set(side * earX, earY, earZ);
    earPivot.rotation.y = -side * (isHermes ? .90 : 1.08);
    root.add(earPivot);
    earPivots.set(side, earPivot);
    const assembly = new THREE.Group();
    assembly.name = `${partner}-${side < 0 ? 'left' : 'right'}-headset`;
    assembly.position.set(-side * earX, -earY, -earZ);
    earPivot.add(assembly);
    cylinder(earRadius * 1.15, width * .08, black, assembly, side * earX, earY, earZ, 'cushioned-ear-seal');
    cylinder(earRadius * 1.10, width * .095, silver, assembly, side * (earX + width * .045), earY, earZ, 'bevelled-machined-ear-shell');
    cylinder(earRadius * .88, width * .102, dark, assembly, side * (earX + width * .08), earY, earZ, 'recessed-ceramic-ear-centre');
    cylinder(earRadius * .63, width * .108, black, assembly, side * (earX + width * .098), earY, earZ, 'polished-ear-lens');
    const externalX = side * (earX + width * .157);
    ring(earRadius * .95, width * .009, silver, assembly, externalX, earY, earZ, 'outer-silver-machined-rim');
    ring(earRadius * .79, width * .009, glow, assembly, externalX + side * width * .003, earY, earZ, 'continuous-luminous-ear-ring');
    ring(earRadius * .61, width * .004, blue, assembly, externalX + side * width * .005, earY, earZ, 'inner-blue-lens-seal');
    for (let i = 0; i < 6; i++) {
      const angle = i * Math.PI / 3 + Math.PI / 6;
      cylinder(width * .009, width * .012, dark, assembly,
        externalX + side * width * .001, earY + Math.cos(angle) * earRadius * .99,
        earZ + Math.sin(angle) * earRadius * .99, 'recessed-torx-fastener');
    }
    details.earphoneLayers += 7;

    // Finely spaced cooling fins are physical ribs rather than painted marks.
    for (let i = 0; i < 5; i++) {
      const angle = -.6 + i * .22;
      tube([
        [side * (earX + width * .09), earY + Math.cos(angle) * earRadius * 1.07, earZ + Math.sin(angle) * earRadius * 1.07],
        [side * (earX + width * .12), earY + Math.cos(angle) * earRadius * 1.13, earZ + Math.sin(angle) * earRadius * 1.13],
      ], width * .0035, paleMetal, assembly, 'ear-case-cooling-rib', 4);
    }

    // Two-piece yoke and luminous cable route behind each ear, leaving the eyes,
    // cheeks and mouth clear for the original model's actual expression morphs.
    tube([
      [side * earX, earY - earRadius * .87, earZ],
      [side * width * .52, earY - height * .25, earZ + width * .03],
      [side * width * .43, chinY + height * .08, faceZ - width * .13],
    ], width * .026, silver, root, 'segmented-jaw-support');
    tube([
      [side * (earX + width * .013), earY - earRadius * .9, earZ + width * .02],
      [side * width * .525, earY - height * .23, earZ + width * .05],
      [side * width * .44, chinY + height * .11, faceZ - width * .12],
    ], width * .006, glow, root, 'luminous-jaw-circuit');

    // Anatomical side armour does not cross the lips, eyelids, brows or nose.
    const cheek = [
      [side * width * .43, eyeY - height * .105],
      [side * width * .50, eyeY - height * .15],
      [side * width * .46, chinY + height * .14],
      [side * width * .37, chinY + height * .08],
      [side * width * .35, chinY + height * .15],
      [side * width * .39, eyeY - height * .19],
    ];
    const cheekPlate = panel(cheek, faceZ - width * .19, width * .025, paleMetal,
      `${side < 0 ? 'left' : 'right'}-articulated-cheek-plate`);
    cheekPlate.rotation.y = side * -.20;
    details.facialSidePanels++;
    const temple = [
      [side * width * .43, eyeY + height * .08],
      [side * width * .49, eyeY + height * .035],
      [side * width * .46, eyeY - height * .055],
      [side * width * .395, eyeY - height * .095],
      [side * width * .385, eyeY - height * .06],
      [side * width * .43, eyeY - height * .015],
    ];
    panel(temple, faceZ - width * .16, width * .018, silver, 'separate-temple-plate');
    details.facialSidePanels++;
    tube([
      [side * width * .40, eyeY - height * .085, faceZ - width * .14],
      [side * width * .44, eyeY - height * .115, faceZ - width * .14],
      [side * width * .445, eyeY - height * .16, faceZ - width * .14],
    ], width * .006, glow, root, 'cheek-inset-light-channel', 10);

    if (isHermes) {
      // Overlapping, bevelled blades describe the swept wings in the reference.
      // Each feather has its own recessed centre and raised silver leading edge.
      const wing = new THREE.Group();
      wing.name = `${side < 0 ? 'left' : 'right'}-layered-metal-wing`;
      wing.position.set(side * (earX + width * .015), earY + earRadius * .57, earZ - width * .08);
      wing.scale.x = side;
      root.add(wing);
      for (let i = 0; i < 6; i++) {
        const rise = height * (.50 - i * .075);
        const reach = width * (.72 - i * .025);
        const y = -height * i * .026;
        const feather = new THREE.Shape();
        feather.moveTo(width * .005, y);
        feather.bezierCurveTo(reach * .32, y + rise * .15, reach * .80, y + rise * .70, reach, y + rise);
        feather.bezierCurveTo(reach * .92, y + rise * .45 - height * .045,
          reach * .49, y + rise * .02 - height * .025, width * .06, y - height * .035);
        feather.quadraticCurveTo(width * .025, y - height * .035, width * .005, y);
        const piece = extrude(feather, width * .019, i % 2 ? paleMetal : silver, wing,
          width * .005, `sculpted-metal-feather-${i + 1}`);
        piece.position.z = -i * width * .026;
        tube([
          [width * .04, y + height * .003, width * .025 - i * width * .026],
          [reach * .41, y + rise * .23, width * .027 - i * width * .026],
          [reach * .86, y + rise * .76, width * .024 - i * width * .026],
        ], width * .0035, i === 0 ? blue : dark, wing, 'feather-recessed-leading-seam', 14);
        details.layeredMetalFeathers++;
      }
      cylinder(earRadius * .40, width * .055, silver, root,
        side * (earX + width * .08), earY + earRadius * .92, earZ - width * .035, 'wing-pivot-housing');
    }
  }

  // The arched crown is inset into the hair instead of replacing the skull.
  const crown = [];
  for (let i = 0; i <= 18; i++) {
    const a = Math.PI * i / 18;
    crown.push([Math.cos(a) * width * .55, eyeY + Math.sin(a) * (topY - eyeY + height * .035), earZ - width * .10]);
  }
  tube(crown, width * .021, black, root, 'padded-headset-crown', 36);
  tube(crown.map(p => [p[0], p[1] + height * .008, p[2] - width * .008]), width * .010, silver, root, 'crown-silver-spline', 36);

  if (isHermes) {
    // Small blue, wing-shaped inlaid forehead glyph, modelled as swept splines.
    const glyphY = eyeY + (topY - eyeY) * .40;
    for (const side of [-1, 1]) {
      tube([[0, glyphY, faceZ + width * .006], [side * width * .065, glyphY + height * .035, faceZ],
        [side * width * .12, glyphY + height * .075, faceZ - width * .012]], width * .005, glow, root, 'forehead-wing-glyph');
      tube([[side * width * .018, glyphY + height * .005, faceZ + width * .008],
        [side * width * .078, glyphY + height * .014, faceZ + width * .003],
        [side * width * .11, glyphY + height * .04, faceZ - width * .006]], width * .004, glow, root, 'forehead-glyph-lower-feather');
    }
  } else {
    // A swept high ponytail with separately tapered, curved locks. No flat cards
    // or bitmap hair substitutes; fine highlight strands follow the same flow.
    const anchor = new THREE.Vector3(width * .10, topY + height * .015, -width * .18);
    const tie = mesh(new THREE.TorusGeometry(width * .12, width * .022, 8, 28), silver, root, 'ponytail-machined-hair-cuff');
    tie.rotation.x = Math.PI * .43;
    tie.position.copy(anchor);
    const tieLight = mesh(new THREE.TorusGeometry(width * .122, width * .007, 6, 28), glow, root, 'ponytail-luminous-cuff-inlay');
    tieLight.rotation.copy(tie.rotation);
    tieLight.position.copy(anchor).add(new THREE.Vector3(0, height * .008, 0));
    const curveSegments = 36;
    const radialSegments = 7;
    for (let lock = 0; lock < 15; lock++) {
      const angle = lock / 15 * Math.PI * 2;
      const spreadX = Math.cos(angle) * width * .21;
      const spreadZ = Math.sin(angle) * width * .13;
      const length = height * (.78 + (lock % 4) * .12);
      const points = [
        anchor.clone().add(new THREE.Vector3(spreadX * .15, 0, spreadZ * .15)),
        anchor.clone().add(new THREE.Vector3(spreadX * .74 + width * .08, height * (.24 + .035 * Math.sin(angle)), -width * .12 + spreadZ)),
        anchor.clone().add(new THREE.Vector3(spreadX * 1.2 + width * .21, height * .15, -width * .34 + spreadZ * 1.4)),
        anchor.clone().add(new THREE.Vector3(spreadX * 1.3 + width * .29, -length * .35, -width * .39 + spreadZ * 1.5)),
        anchor.clone().add(new THREE.Vector3(spreadX * 1.2 + width * .23, -length * .78, -width * .24 + spreadZ * 1.4)),
        anchor.clone().add(new THREE.Vector3(spreadX * .8 + width * .35, -length, -width * .08 + spreadZ)),
      ];
      const curve = new THREE.CatmullRomCurve3(points);
      const frames = curve.computeFrenetFrames(curveSegments, false);
      const positions = [], normals = [], uv = [], indices = [];
      const radius = width * (.034 + (lock % 3) * .006);
      for (let i = 0; i <= curveSegments; i++) {
        const t = i / curveSegments;
        const point = curve.getPointAt(t);
        const taper = Math.pow(1 - t, .58) * .94 + .015;
        for (let j = 0; j <= radialSegments; j++) {
          const a = j / radialSegments * Math.PI * 2;
          const normal = frames.normals[i].clone().multiplyScalar(Math.cos(a)).addScaledVector(frames.binormals[i], Math.sin(a));
          const position = point.clone().addScaledVector(normal, radius * taper);
          positions.push(...position.toArray()); normals.push(...normal.toArray()); uv.push(j / radialSegments, t);
          if (i < curveSegments && j < radialSegments) {
            const k = i * (radialSegments + 1) + j;
            indices.push(k, k + 1, k + radialSegments + 1, k + 1, k + radialSegments + 2, k + radialSegments + 1);
          }
        }
      }
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
      geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
      geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
      geometry.setIndex(indices);
      mesh(geometry, lock % 3 ? hair : hairHighlight, root, `individually-tapered-ponytail-lock-${lock + 1}`);
      details.hairLocks++;
      if (lock % 3 === 0) {
        const highlight = [];
        for (let i = 0; i < 18; i++) {
          const t = i / 19;
          const point = curve.getPointAt(t);
          point.z += radius * Math.pow(1 - t, .6);
          highlight.push(point.toArray());
        }
        tube(highlight, width * .0023, hairHighlight, root, 'ponytail-lock-highlight-ridge', 24);
      }
    }

    // A six-fold luminous headset rosette is physical wirework on the forward
    // edge of both earcups; its projected signature remains legible in profile.
    for (const side of [-1, 1]) {
      const emblem = new THREE.Group();
      emblem.position.set(side * width * .165, 0, 0);
      emblem.rotation.y = Math.PI / 2;
      earPivots.get(side).add(emblem);
      for (let i = 0; i < 6; i++) {
        const angle = i * Math.PI / 3;
        const loop = mesh(new THREE.TorusGeometry(earRadius * .22, width * .0045, 6, 24, Math.PI * 1.75), glow, emblem, 'six-fold-headset-emblem-loop');
        loop.position.set(Math.cos(angle) * earRadius * .21, Math.sin(angle) * earRadius * .21, width * .003 * (i % 2));
        loop.rotation.z = angle;
      }
    }
  }

  root.updateMatrixWorld(true);
  let meshCount = 0, triangles = 0;
  root.traverse(object => {
    if (object.isMesh) {
      meshCount++;
      triangles += object.geometry.index ? object.geometry.index.count / 3 : object.geometry.attributes.position.count / 3;
      object.userData.referenceDesign = partner;
    }
  });
  details.meshCount = meshCount;
  details.triangles = triangles;
  details.originalModel = vrm.meta?.name || vrm.meta?.title || null;
  return {
    root,
    details,
    // All geometry is rigidly attached to the animated head frame. Facial
    // expressions remain driven by the original, independently controlled VRM.
    update(state) {
      root.visible = state?.visible !== false;
    },
  };
}
