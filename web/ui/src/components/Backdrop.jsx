import { useEffect, useRef } from 'react'

/**
 * The portfolio's backdrop, the same field on the same shader: topographic
 * contour lines drifting through a slow noise field, pink at the top of the page
 * and indigo at the bottom. It replaces the static bg-gradient.svg wash, which
 * was an approximation of this one made before the page could run it.
 *
 * The fragment shader below is ../../../../portfolio/src/components/Backdrop.jsx
 * verbatim — the two must stay identical or the sites drift apart, so if one is
 * edited the other is too.
 *
 * What is *not* copied is the plumbing. The portfolio drives its quad with
 * three.js; here it is thirty lines of raw WebGL, because everything three does
 * on this page is hand a fullscreen triangle pair to one program. There is no
 * scene, no camera, no geometry and no material to manage, so the library is
 * ~160KB gzipped of nothing — and this page is a dashboard someone opens on a
 * phone, on a home server, where that is the whole bundle over again.
 */
const FRAG = /* glsl */ `
  #extension GL_OES_standard_derivatives : enable
  precision highp float;
  uniform vec2  uRes;      // device pixels, matching gl_FragCoord
  uniform float uTime;
  uniform float uScroll;   // eased + compressed page progress
  uniform vec2  uPointer;  // already scaled down by the caller

  vec3 mod289(vec3 x){return x-floor(x*(1./289.))*289.;}
  vec2 mod289(vec2 x){return x-floor(x*(1./289.))*289.;}
  vec3 permute(vec3 x){return mod289(((x*34.)+1.)*x);}
  float snoise(vec2 v){
    const vec4 C=vec4(0.211324865,0.366025404,-0.577350269,0.024390243);
    vec2 i=floor(v+dot(v,C.yy)), x0=v-i+dot(i,C.xx);
    vec2 i1=(x0.x>x0.y)?vec2(1.,0.):vec2(0.,1.);
    vec4 x12=x0.xyxy+C.xxzz; x12.xy-=i1;
    i=mod289(i);
    vec3 p=permute(permute(i.y+vec3(0.,i1.y,1.))+i.x+vec3(0.,i1.x,1.));
    vec3 m=max(0.5-vec3(dot(x0,x0),dot(x12.xy,x12.xy),dot(x12.zw,x12.zw)),0.);
    m=m*m; m=m*m;
    vec3 x=2.*fract(p*C.www)-1., h=abs(x)-0.5, ox=floor(x+0.5), a0=x-ox;
    m*=1.79284291-0.85373472*(a0*a0+h*h);
    vec3 g;
    g.x=a0.x*x0.x+h.x*x0.y;
    g.yz=a0.yz*x12.xz+h.yz*x12.yw;
    return 130.*dot(m,g);
  }
  float fbm(vec2 p){ return snoise(p)*0.6 + snoise(p*2.1)*0.3; }

  void main(){
    vec2 uv = (gl_FragCoord.xy - 0.5*uRes) / uRes.y;
    vec2 q  = uv + uPointer * 0.06;
    float n = fbm(q * 1.3 + uTime * 0.04);

    // Bands tighten as you descend — reads as pressure/depth.
    float freq  = mix(9.0, 22.0, uScroll);
    float bands = fract(n * freq + uScroll * 2.0);
    // Width-corrected so lines stay ~1px however steep the noise gradient is.
    float d    = abs(bands - 0.5);
    float line = 1.0 - smoothstep(0.0, fwidth(n * freq) * 1.2, d);

    vec3 ink   = vec3(0.063, 0.063, 0.078);
    vec3 slate = vec3(0.114, 0.114, 0.141);
    // Pink near the top of the page, indigo toward the bottom.
    vec3 hue   = mix(vec3(0.925,0.282,0.600), vec3(0.290,0.376,0.921),
                     smoothstep(0.2, 0.9, uScroll));

    vec3 col = mix(ink, slate, smoothstep(-0.4, 0.5, n));
    col += hue * line * 0.45;

    col *= 1.0 - 0.5 * pow(length(uv * vec2(0.62, 1.0)), 2.2);
    // Quiet down past the hero so project cards keep their contrast.
    col *= 1.0 - 0.35 * smoothstep(0.05, 0.55, uScroll);
    // 8-bit gradients band badly on near-black.
    col += (fract(sin(dot(gl_FragCoord.xy, vec2(12.9898,78.233))) * 43758.5453) - 0.5) / 255.0;

    gl_FragColor = vec4(col, 1.0);
  }
`

const VERT = 'attribute vec2 p; void main(){ gl_Position = vec4(p, 0.0, 1.0); }'

function compile(gl, type, source) {
  const shader = gl.createShader(type)
  gl.shaderSource(shader, source)
  gl.compileShader(shader)
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    throw new Error(gl.getShaderInfoLog(shader))
  }
  return shader
}

export default function Backdrop() {
  const canvasRef = useRef(null)
  const input = useRef({ x: 0, y: 0, tx: 0, ty: 0, scroll: 0 })

  useEffect(() => {
    const onMove = (e) => {
      input.current.tx = (e.clientX / window.innerWidth) * 2 - 1
      input.current.ty = -((e.clientY / window.innerHeight) * 2 - 1)
    }
    const onScroll = () => {
      const max = document.body.scrollHeight - window.innerHeight
      input.current.scroll = max > 0 ? window.scrollY / max : 0
    }
    onScroll()
    window.addEventListener('pointermove', onMove, { passive: true })
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
    }
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const gl = canvas.getContext('webgl', { antialias: true })
    // `fwidth` is what keeps a contour one pixel wide however steep the noise
    // gets under it; without derivatives the shader will not even compile. A
    // context this old is not worth a second code path — the page keeps the
    // flat --paper it already has behind this canvas.
    if (!gl || !gl.getExtension('OES_standard_derivatives')) return

    let program
    try {
      program = gl.createProgram()
      gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, VERT))
      gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, FRAG))
      gl.linkProgram(program)
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
        throw new Error(gl.getProgramInfoLog(program))
      }
    } catch {
      return
    }
    gl.useProgram(program)

    // One triangle pair covering clip space. It never changes, so it is uploaded
    // once and the draw call below just replays it.
    const buffer = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW)
    const p = gl.getAttribLocation(program, 'p')
    gl.enableVertexAttribArray(p)
    gl.vertexAttribPointer(p, 2, gl.FLOAT, false, 0, 0)

    const uRes = gl.getUniformLocation(program, 'uRes')
    const uTime = gl.getUniformLocation(program, 'uTime')
    const uScroll = gl.getUniformLocation(program, 'uScroll')
    const uPointer = gl.getUniformLocation(program, 'uPointer')

    const resize = () => {
      // Retina is invisible here and doubles the fragment cost.
      const dpr = Math.min(window.devicePixelRatio, 2)
      canvas.width = Math.round(window.innerWidth * dpr)
      canvas.height = Math.round(window.innerHeight * dpr)
      gl.viewport(0, 0, canvas.width, canvas.height)
      gl.uniform2f(uRes, canvas.width, canvas.height)
    }
    resize()
    window.addEventListener('resize', resize)

    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const start = performance.now()
    let last = start
    let raf
    let easedScroll = 0

    const frame = (now) => {
      raf = requestAnimationFrame(frame)
      const dt = (now - last) / 1000
      last = now
      const q = input.current

      gl.uniform1f(uTime, (now - start) / 1000)
      // Slow follow + heavy scale: the field answers the cursor, not tracks it.
      q.x += (q.tx - q.x) * Math.min(dt * 1.1, 1)
      q.y += (q.ty - q.y) * Math.min(dt * 1.1, 1)
      gl.uniform2f(uPointer, q.x * 0.28, q.y * 0.28)
      // Ease the scroll value so flicks glide instead of snapping.
      easedScroll += (q.scroll - easedScroll) * Math.min(dt * 3.5, 1)
      gl.uniform1f(uScroll, easedScroll)

      gl.drawArrays(gl.TRIANGLES, 0, 3)
      // Reduced motion still gets one composed frame, just frozen.
      if (reduce) cancelAnimationFrame(raf)
    }
    raf = requestAnimationFrame(frame)

    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', resize)
      gl.deleteBuffer(buffer)
      gl.deleteProgram(program)
      gl.getExtension('WEBGL_lose_context')?.loseContext()
    }
  }, [])

  return <canvas ref={canvasRef} aria-hidden className="backdrop" />
}
