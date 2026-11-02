/* NEXUS I-Robot v8 – Enhancer für /m: nur Hintergrund, Partikel, Parallax. Keine Funktionsänderung. */
(function(){const NX=window.NXR;if(!NX)return;document.body.insertAdjacentHTML('afterbegin','<div class="nx-bg"></div><canvas id="nxParticles"></canvas>');
NX.evalLite().then(()=>{NX.parallax();NX.particles(document.getElementById('nxParticles'));});})();
