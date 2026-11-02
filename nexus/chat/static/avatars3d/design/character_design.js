import * as THREE from 'three';
import { buildNexus } from './nexus.js?v=005d68g3';
import { buildAndroid } from './androids.js?v=005d68g3';

// Reference-specific costume layers on the original, unmodified CC0 skin rigs.
// Bone-attached geometry uses metres in a neutral, forward-positive head frame.
const MEASUREMENTS = {
  nexus: {headWidth:.223735, headHeight:.244285, headTopY:.195067, hairTopY:.261284, chinY:-.049218, eyeY:.062553, faceZ:.121393},
  hermes: {headWidth:.215492, headHeight:.235285, headTopY:.187880, hairTopY:.237355, chinY:-.047405, eyeY:.060225, faceZ:.116920},
  gpt: {headWidth:.215384, headHeight:.233705, headTopY:.200317, hairTopY:.246637, chinY:-.033388, eyeY:.064535, faceZ:.115810},
};
let environment;
function studioEnvironment() {
  if (environment) return environment;
  const width=256, height=128, data=new Uint8Array(width*height*4);
  for (let y=0; y<height; y++) for (let x=0; x<width; x++) {
    const u=x/width, v=y/height;
    const softbox = Math.pow(Math.max(0,Math.cos((u-.23)*Math.PI*2)),30)*Math.exp(-Math.pow((v-.39)*5,2));
    const rim = Math.pow(Math.max(0,Math.cos((u-.72)*Math.PI*2)),65)*Math.exp(-Math.pow((v-.46)*4,2));
    const horizon = Math.exp(-Math.pow((v-.5)*10,2));
    const k=(y*width+x)*4;
    data[k]=Math.min(255,18+softbox*225+rim*110+horizon*13);
    data[k+1]=Math.min(255,28+softbox*220+rim*175+horizon*22);
    data[k+2]=Math.min(255,46+softbox*209+rim*209+horizon*35);
    data[k+3]=255;
  }
  environment=new THREE.DataTexture(data,width,height,THREE.RGBAFormat);
  environment.mapping=THREE.EquirectangularReflectionMapping;
  environment.colorSpace=THREE.SRGBColorSpace; environment.needsUpdate=true;
  return environment;
}
function textureVariant(source, mode, partner) {
  if (!source?.image) return source || null;
  const image=source.image, canvas=document.createElement('canvas');
  canvas.width=image.width; canvas.height=image.height;
  const context=canvas.getContext('2d',{willReadFrequently:true});
  context.drawImage(image,0,0);
  const pixels=context.getImageData(0,0,canvas.width,canvas.height), a=pixels.data;
  for (let i=0;i<a.length;i+=4) {
    const l=.2126*a[i]+.7152*a[i+1]+.0722*a[i+2];
    if (mode==='hair') {
      const v=102+Math.pow(l/255,.7)*132;
      a[i]=v*(partner==='gpt'?1.035:1); a[i+1]=v*(partner==='gpt'?.985:1.015); a[i+2]=v*(partner==='gpt'?.87:1.045);
    } else if (mode==='face') {
      const v=partner==='nexus'?l*.92:28+l*.86;
      a[i]=v*.94; a[i+1]=v*.985; a[i+2]=v;
    } else if (mode==='iris') {
      a[i]=l*.24; a[i+1]=l*.68; a[i+2]=Math.min(255,l*1.10);
    } else if (mode==='suit') {
      a[i]=l*.64; a[i+1]=l*.77; a[i+2]=l*.91;
    }
  }
  context.putImageData(pixels,0,0);
  const texture=new THREE.CanvasTexture(canvas);
  texture.flipY=source.flipY; texture.colorSpace=THREE.SRGBColorSpace;
  texture.wrapS=source.wrapS;texture.wrapT=source.wrapT;
  texture.repeat.copy(source.repeat);texture.offset.copy(source.offset);
  texture.name=`${partner}-${mode}-${source.name || 'albedo'}`;
  return texture;
}
function materialsFor(partner) {
  const metal=(color,metalness=.78,roughness=.32)=>new THREE.MeshStandardMaterial({color,metalness,roughness,envMapIntensity:1.1});
  const glow=color=>new THREE.MeshStandardMaterial({color,emissive:color,emissiveIntensity:1.25,metalness:.25,roughness:.26,toneMapped:false});
  const cloth=new THREE.MeshStandardMaterial({color:0x080e17,roughness:.94,side:THREE.DoubleSide});
  const weave=document.createElement('canvas');weave.width=128;weave.height=128;
  const ctx=weave.getContext('2d');ctx.fillStyle='#777';ctx.fillRect(0,0,128,128);
  for(let i=0;i<128;i+=4){ctx.fillStyle=i%8?'#666':'#aaa';ctx.fillRect(i,0,1,128);ctx.fillRect(0,i,128,1);}
  const bump=new THREE.CanvasTexture(weave);bump.wrapS=bump.wrapT=THREE.RepeatWrapping;bump.repeat.set(5,5);
  cloth.bumpMap=bump;cloth.bumpScale=.0007;
  return {silver:metal(0xbac7d2),ivory:metal(0xd8dce4,.62,.31),dark:metal(0x172532,.66,.3),
    black:metal(0x040914,.45,.23),blue:metal(0x10547d,.65,.24),
    glowBlue:glow(0x1eb8ff),glowViolet:glow(0x8c5eff),cloth};
}
function resurface(vrm,partner) {
  const cache=new Map(), variants=new Map(), changed=[];
  const variant=(texture,mode)=>{
    const key=texture?.uuid+mode;
    if(!variants.has(key))variants.set(key,textureVariant(texture,mode,partner));
    return variants.get(key);
  };
  vrm.scene.traverse(object=>{
    if(!object.isMesh)return;
    const original=Array.isArray(object.material)?object.material:[object.material];
    // MToon outline draws are redundant after conversion to lit physical surfaces.
    if(original.every(m=>m.name.includes('(Outline)'))){object.visible=false;return;}
    const convert=old=>{
      if(cache.has(old))return cache.get(old);
      const name=old.name, hair=/HAIR/i.test(name), face=/Face_00_SKIN/.test(name), body=/Body.*SKIN/.test(name);
      const suit=/CLOTH/.test(name), iris=/EyeIris/.test(name);
      const mat=new THREE.MeshStandardMaterial({name:`${partner}-android-${name}`,color:old.color?.clone()||new THREE.Color(0xffffff),
        map:old.map||null,transparent:old.transparent,opacity:old.opacity,alphaTest:old.alphaTest||0,
        side:old.side,depthWrite:old.depthWrite,roughness:.46,metalness:.04});
      if(hair){mat.color.set(0xffffff);mat.map=variant(old.map,'hair');mat.roughness=.37;mat.metalness=.24;if(partner==='nexus')mat.visible=false;}
      else if(face||body){mat.color.set(partner==='nexus'?0xb3c2d1:partner==='gpt'?0xe0dfe3:0xd8dfe6);mat.map=variant(old.map,'face');mat.metalness=partner==='nexus'?.65:.30;mat.roughness=.36;}
      else if(suit){mat.color.set(partner==='gpt'?0x777d97:0x263344);mat.map=variant(old.map,'suit');mat.metalness=.55;mat.roughness=.34;}
      else if(iris){mat.color.set(0x8acaff);mat.map=variant(old.map,'iris');mat.emissive.set(0x0069b4);mat.emissiveIntensity=.55;mat.metalness=.3;mat.roughness=.18;}
      else if(/FaceBrow/.test(name)){mat.color.set(partner==='nexus'?0x17212b:0x8e9ba7);}
      else if(/FaceEye(line|lash)/.test(name)){mat.color.set(0x111c2d);}
      else if(/FaceMouth/.test(name)){mat.color.set(0x71818e);mat.roughness=.4;}
      mat.envMapIntensity=.9;cache.set(old,mat);
      changed.push({name,kind:hair?'hair':face?'face':body?'body':suit?'suit':iris?'iris':'detail',visible:mat.visible});
      return mat;
    };
    object.material=Array.isArray(object.material)?original.map(convert):convert(original[0]);
  });
  return changed;
}
function boneFrame(vrm,name) {
  const bone=vrm.humanoid.getRawBoneNode(name);
  if(!bone)return null;
  bone.updateWorldMatrix(true,false);
  const frame=new THREE.Group();frame.name=`reference-${name}-frame`;
  frame.quaternion.copy(bone.getWorldQuaternion(new THREE.Quaternion()).invert());
  bone.add(frame);return frame;
}
function bodyArmour(vrm,partner,materials) {
  const chest=boneFrame(vrm,'upperChest')||boneFrame(vrm,'chest');
  if(!chest)return {panels:0};
  const head=vrm.humanoid.getRawBoneNode('head').getWorldPosition(new THREE.Vector3());
  const chestWorld=chest.getWorldPosition(new THREE.Vector3());
  const top=head.y-chestWorld.y-.18;
  const front=.105, glow=partner==='gpt'?materials.glowViolet:materials.glowBlue;
  let panels=0;
  const panel=(points,z,material,name)=>{
    const shape=new THREE.Shape();points.forEach(([x,y],i)=>i?shape.lineTo(x,y):shape.moveTo(x,y));shape.closePath();
    const geo=new THREE.ExtrudeGeometry(shape,{depth:.014,bevelEnabled:true,bevelSegments:3,bevelSize:.005,bevelThickness:.004,steps:1});
    // Wrap the machined plates around the chest instead of using flat shields.
    const positions=geo.attributes.position;
    for(let i=0;i<positions.count;i++) {
      const x=positions.getX(i),y=positions.getY(i);
      positions.setZ(i,positions.getZ(i)+.027*Math.max(0,1-(x/.19)**2)-.008*((y-top)/.3)**2);
    }
    geo.computeVertexNormals();
    const mesh=new THREE.Mesh(geo,material);mesh.position.z=z;mesh.name=name;chest.add(mesh);panels++;return mesh;
  };
  for(const side of [-1,1]) {
    const x=v=>side*v;
    panel([[x(.012),top],[x(.093),top+.035],[x(.178),top-.018],[x(.155),top-.125],[x(.044),top-.16],[x(.016),top-.105]],front,partner==='nexus'?materials.dark:materials.silver,'contoured-pectoral-armour');
    panel([[x(.035),top-.02],[x(.10),top+.005],[x(.157),top-.033],[x(.147),top-.052],[x(.08),top-.025]],front+.021,materials.ivory,'bevelled-clavicle-plate');
    panel([[x(.024),top-.10],[x(.11),top-.125],[x(.12),top-.136],[x(.031),top-.117]],front+.02,glow,'pectoral-inlaid-light-channel');
    for(let i=0;i<3;i++)panel([[x(.035),top-.18-i*.045],[x(.125-i*.008),top-.17-i*.045],[x(.122-i*.01),top-.197-i*.045],[x(.03),top-.212-i*.045]],front-.01,materials.dark,'segmented-abdominal-plate');
  }
  const insignia=new THREE.Mesh(new THREE.TorusGeometry(.027,.0027,7,40),glow);insignia.position.set(0,top-.08,front+.032);insignia.name='reactor-insignia-ring';chest.add(insignia);
  const core=new THREE.Mesh(new THREE.OctahedronGeometry(.014,0),materials.silver);core.position.copy(insignia.position);core.name='reactor-core';chest.add(core);
  for(const [boneName,side]of[['leftUpperArm',-1],['rightUpperArm',1]]) {
    const frame=boneFrame(vrm,boneName);if(!frame)continue;
    const shoulder=new THREE.Mesh(new THREE.SphereGeometry(.085,28,18,0,Math.PI*2,0,Math.PI*.6),materials.silver);
    shoulder.scale.set(1,.65,1.12);shoulder.position.set(side*.025,-.025,0);shoulder.name='sculpted-shoulder-pauldron';frame.add(shoulder);
    const trim=new THREE.Mesh(new THREE.TorusGeometry(.074,.003,6,32,Math.PI),glow);trim.rotation.x=-Math.PI/2;trim.position.copy(shoulder.position).add(new THREE.Vector3(0,.025,0));trim.name='shoulder-arc-inlay';frame.add(trim);
  }
  return {panels,frame:chest.name};
}
export function applyCharacterDesign(vrm,partner,scene) {
  const measurements={...MEASUREMENTS[partner]},materials=materialsFor(partner);
  scene.environment=studioEnvironment();scene.environmentIntensity=1.05;
  const changed=resurface(vrm,partner);
  // Pose only the normalized rig: preserve raw joint offsets, bind matrices,
  // hierarchy and skin weights. Hands hang beside the hips, elbows slightly bent.
  const armPose={};
  for(const [side,sign] of [['left',1],['right',-1]]) {
    for(const [part,angles] of [['UpperArm',[0,0,sign*1.38]],['LowerArm',[0,sign*.12,sign*.055]]]) {
      const name=side+part,bone=vrm.humanoid.getNormalizedBoneNode(name);
      if(!bone)continue;
      const rest=bone.quaternion.clone();
      bone.quaternion.copy(rest).multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(...angles)));
      armPose[name]={rest:rest.toArray(),posed:bone.quaternion.toArray()};
    }
  }
  vrm.update(0);vrm.scene.updateMatrixWorld(true);
  const headFrame=boneFrame(vrm,'head'),rig={head:vrm.humanoid.getRawBoneNode('head'),headFrame,measurements};
  const costume=partner==='nexus'?buildNexus({THREE,vrm,rig,materials}):buildAndroid({THREE,vrm,partner,rig,materials});
  headFrame.add(costume.root);
  const armour=bodyArmour(vrm,partner,materials);
  vrm.scene.updateMatrixWorld(true);
  const reference=vrm.humanoid.getNormalizedBoneNode('leftEye').quaternion.clone();
  const details={...costume.details,partner,measurements,armour,armPose,materials:changed,version:'005d68g3',
    originalFileUnchanged:true,headFrame:headFrame.uuid,headBone:rig.head.uuid};
  return {details,root:costume.root,update(expressions){
    const q=reference.clone().invert().multiply(vrm.humanoid.getNormalizedBoneNode('leftEye').quaternion);
    const angles=new THREE.Euler().setFromQuaternion(q,'YXZ');
    costume.update({expressions,gaze:{yaw:angles.y,pitch:angles.x}});
  }};
}
