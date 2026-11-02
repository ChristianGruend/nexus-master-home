/* Registration is in original-image pixels, before responsive scaling/cropping. */
(function () {
  "use strict";
  const visemes = Object.freeze(["neutral", "AI", "E", "O", "U", "MBP", "FV"]);
  function eye(center, angle, points, fill, lidOffset, gaze) {
    const a = -angle * Math.PI / 180;
    const local = points.map(([x, y]) => [
      (x - center[0]) * Math.cos(a) - (y - center[1]) * Math.sin(a),
      (x - center[0]) * Math.sin(a) + (y - center[1]) * Math.cos(a)]);
    const xs = local.map(p => p[0]), ys = local.map(p => p[1]);
    return Object.freeze({center, angle, fill, lidOffset, gaze,
      path: "M " + local.map(p => p.map(v => v.toFixed(3)).join(" ")).join(" L ") + " Z",
      left: Math.min(...xs) - 1, top: Math.min(...ys) - 1,
      width: Math.max(...xs) - Math.min(...xs) + 2,
      height: Math.max(...ys) - Math.min(...ys) + 2});
  }
  function rig(partner, eyes, mouth, size, canvas) {
    return Object.freeze({partner, fallback: partner === "aether" ? "/static/assets/avatars/aether.png" :
      "/static/assets/master-glass/reference-final/" + partner + ".png",
      size: Object.freeze(size || [1254, 1254]), canvas: Object.freeze(canvas || size || [1254, 1254]), visemes,
      face: Object.freeze({eyes: Object.freeze(eyes), mouth: Object.freeze(mouth)})});
  }
  window.NEXUS_AVATAR_RIGS = Object.freeze({
    nexus: rig("nexus", [
      eye([531.512, 331.332], 12, [[483.193, 310.624], [506.202, 310.624], [543.017, 324.429], [572.928, 338.235], [572.928, 347.439], [533.813, 338.235], [494.697, 329.031]], "#061120", 0.0, 0.69),
      eye([671.868, 331.332], -12, [[632.752, 338.235], [662.664, 324.429], [701.78, 308.323], [715.585, 310.624], [701.78, 326.73], [662.664, 338.235], [632.752, 347.439]], "#061120", 0.0, 0.69)
    ], {"center": [605.141, 501.6], "angle": 0, "width": 27.611, "height": 6.903, "kind": "visor"}, [1254, 998], [1254, 1400]),
    hermes: rig("hermes", [
      eye([614.481, 318.919], 6, [[583.519, 309.63], [602.096, 300.341], [626.867, 306.533], [648.541, 318.919], [629.963, 334.4], [605.193, 331.304]], "#b9c7d5", -15.481, 0.929),
      eye([760.007, 340.593], 6, [[732.141, 334.4], [753.815, 325.111], [775.489, 331.304], [787.874, 343.689], [769.296, 356.074], [747.622, 352.978]], "#b7c8d8", -15.481, 0.929)
    ], {"center": [667.119, 452.059], "angle": 2, "width": 40.252, "height": 8.67, "kind": "lips"}, [1254, 1253], [1254, 1400]),
    gpt: rig("gpt", [
      eye([497.063, 377.417], -9, [[472.714, 374.374], [490.976, 365.243], [512.282, 365.243], [530.544, 374.374], [512.282, 389.592], [487.932, 392.636]], "#c1c6ce", -15.218, 0.913),
      eye([627.942, 346.981], -9, [[597.505, 343.937], [615.767, 331.762], [640.117, 331.762], [658.379, 340.893], [643.16, 356.112], [618.811, 359.155]], "#c1c6ce", -15.218, 0.913)
    ], {"center": [567.068, 471.772], "angle": -8, "width": 36.524, "height": 7.914, "kind": "lips"}, [1254, 1253], [1254, 1400]),
    aether: rig("aether", [
      {center: [565, 462], angle: 0, width: 76, height: 32,
        left: -38, top: -16,
        path: "M -38 0 A 38 16 0 1 1 38 0 A 38 16 0 1 1 -38 0 Z",
        fill: "#327691", lidOffset: 34, gaze: 2.2},
      {center: [690, 462], angle: 0, width: 76, height: 32,
        left: -38, top: -16,
        path: "M -38 0 A 38 16 0 1 1 38 0 A 38 16 0 1 1 -38 0 Z",
        fill: "#327691", lidOffset: 34, gaze: 2.2}
    ], {center: [627, 620], angle: 0, width: 92, height: 38, kind: "drawn"})
  });
})();
