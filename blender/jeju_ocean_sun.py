# -*- coding: utf-8 -*-
"""
제주도 지형 + 출렁이는 바다 + 움직이는 태양  (Blender 4.2 ~ 5.x 용 파이썬 스크립트)

[무엇을 하나요?]
  1) 제주도 DEM(높이 이미지)으로 섬 지형을 만듭니다.
     DEM 파일이 없으면 한라산을 흉내 낸 '가짜 지형'으로 대신 만듭니다.
  2) 섬 주변에 Ocean(바다) 모디파이어로 출렁이는 바다를 깝니다.
  3) 하늘(Sky Texture)과 태양(Sun 라이트)을 두고,
     태양이 동쪽에서 떠서 남쪽 하늘을 지나 서쪽으로 지도록 애니메이션합니다.
     태양이 움직이면 바다 위 햇빛 반사(글린트)도 함께 움직입니다.
  4) 카메라, 프레임 범위, 렌더 엔진, 해상도를 설정합니다.

[좌표 약속]
  - 1 Blender 단위(1m 칸) = 실제 1 km 로 생각합니다.
  - +X = 동쪽, +Y = 북쪽, +Z = 위쪽.
  - 바다 수면 = Z 0 (해발 0 m).

[실행 방법] 같은 폴더의 README_KO.md 를 보세요.
"""

import math
import os
import random

import bpy
import numpy as np
from mathutils import Vector

# =====================================================================
# 1. 설정 (여기만 바꾸면 됩니다!)
# =====================================================================

# 제주도 DEM 파일 경로. 비워 두거나("") 파일이 없으면 가짜 지형으로 실행됩니다.
# 예) DEM_PATH = r"C:\vibe_advanced\day1\blender\jeju_dem.tif"
DEM_PATH = r""

DEM_WIDTH_KM = 80.0        # DEM 이미지의 가로 폭이 실제로 몇 km 인지 (제주도 전체 ≈ 75~90 km)
DEM_MAX_HEIGHT_M = 1947.0  # 이미지 값이 0~1(흑백 그림)일 때, 가장 흰 곳의 실제 높이(m). 한라산 = 1947 m
DEM_NODATA_BELOW_M = -1000.0  # 이 값보다 낮은 높이는 '데이터 없음'으로 보고 바다로 처리
GRID_RES = 300             # 지형 메시의 가로 방향 점 개수(클수록 자세하지만 느려짐)

VERTICAL_EXAGGERATION = 3.0  # 높이 과장 배율 (실제 비율이면 섬이 너무 납작해 보여서 키움)
SEA_LEVEL_M = 0.0            # 해수면 높이(m). 이보다 낮은 땅은 바닷속에 잠깁니다.
UNDERWATER_DEPTH_M = -60.0   # 바닷속에 잠긴 땅을 이 깊이(m)로 내려 둡니다.

# 애니메이션
FPS = 24
FRAME_START = 1
FRAME_END = 240              # 240 프레임 = 10초
WAVE_SPEED = 1.0             # 물결이 움직이는 속도 배율
SUN_MIN_ELEVATION_DEG = 3.0  # 해 뜰 때/질 때 태양 고도(도)
SUN_MAX_ELEVATION_DEG = 25.0 # 한낮(정남쪽)의 태양 고도(도)

# 렌더
RENDER_ENGINE = "CYCLES"     # "CYCLES"(사실적, 느림) 또는 "EEVEE"(빠름)
RES_X, RES_Y = 1920, 1080
RES_PERCENT = 50             # 시험용 50%. 최종 렌더는 100으로
CYCLES_SAMPLES = 64
OUTPUT_PATH = "//render/jeju_"   # '//' = .blend 파일이 있는 폴더 기준
OUTPUT_AS_VIDEO = True           # True: mp4 동영상, False: PNG 그림 여러 장

CLEAR_SCENE = True           # 실행할 때 기존 오브젝트를 모두 지울지

KM = 1.0                     # 1 km 를 Blender 몇 단위로 쓸지 (바꾸지 않는 것을 추천)


# =====================================================================
# 2. 작은 도우미 함수들
# =====================================================================

def log(msg):
    """진행 상황을 화면(시스템 콘솔)에 출력합니다."""
    print("[제주 스크립트] " + msg)


def meters_to_z(h_m):
    """실제 높이(m) → Blender 의 Z 값 (높이 과장 포함)."""
    return h_m / 1000.0 * KM * VERTICAL_EXAGGERATION


def clear_scene():
    """장면의 오브젝트와 쓰지 않는 데이터를 정리합니다."""
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.lights, bpy.data.cameras,
                 bpy.data.materials, bpy.data.worlds):
        for block in list(coll):
            if block.users == 0:
                coll.remove(block)


def new_material(name):
    """노드를 쓰는 새 재질을 만들고, 노드를 비운 상태로 돌려줍니다."""
    mat = bpy.data.materials.new(name)
    if bpy.app.version < (5, 0, 0):
        mat.use_nodes = True       # Blender 4.x 에서는 필요 (5.x 는 항상 켜져 있음)
    mat.node_tree.nodes.clear()
    return mat


def set_input(node, names, value):
    """노드 입력 이름이 버전마다 달라서, 후보 이름 중 있는 것에 값을 넣습니다."""
    for n in names:
        if n in node.inputs:
            node.inputs[n].default_value = value
            return True
    return False


# =====================================================================
# 3. 높이맵 준비 (DEM 또는 가짜 지형)
# =====================================================================

def load_dem(path):
    """DEM 이미지를 읽어 (높이[m] 2차원 배열, 가로 km, 세로 km)를 돌려줍니다."""
    img = bpy.data.images.load(path, check_existing=False)
    try:
        img.colorspace_settings.name = "Non-Color"  # 색 보정 없이 '숫자 그대로' 읽기
    except Exception:
        pass
    w, h = img.size
    ch = img.channels
    px = np.empty(w * h * ch, dtype=np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    # Blender 이미지는 아래쪽(남쪽) 줄부터 저장 → 배열 0번 줄 = 남쪽
    heights = px.reshape(h, w, ch)[:, :, 0].astype(np.float64)

    valid = heights > DEM_NODATA_BELOW_M   # 예: -9999 같은 '데이터 없음' 표시 걸러내기
    if not valid.any():
        raise ValueError("DEM 에 쓸 수 있는 높이 값이 없습니다")
    if heights[valid].max() <= 1.0:
        # 0~1 로 저장된 흑백 높이 그림 → 실제 높이(m)로 바꾸기
        log("DEM 값이 0~1 범위 → 흑백 높이 그림으로 보고 최고 %.0f m 로 늘립니다." % DEM_MAX_HEIGHT_M)
        heights = heights * DEM_MAX_HEIGHT_M
    else:
        log("DEM 값을 실제 미터(m) 단위로 사용합니다. 최고 높이 = %.0f m" % heights[valid].max())
    heights[~valid] = UNDERWATER_DEPTH_M  # 데이터 없는 칸 = 바다

    # 점 개수를 GRID_RES 정도로 줄이기 (너무 크면 느려지므로)
    step = max(1, int(math.ceil(max(w, h) / GRID_RES)))
    heights = heights[::step, ::step]
    width_km = DEM_WIDTH_KM
    height_km = DEM_WIDTH_KM * h / w
    log("DEM 크기 %d x %d 픽셀 → 메시 %d x %d 점" % (w, h, heights.shape[1], heights.shape[0]))
    return heights, width_km, height_km


def fake_jeju_heights():
    """DEM 이 없을 때 쓰는 '제주도 흉내' 높이맵 (단위 m)."""
    width_km, height_km = 80.0, 40.0
    nx = GRID_RES
    ny = int(GRID_RES * height_km / width_km)
    xs = np.linspace(-width_km / 2, width_km / 2, nx)
    ys = np.linspace(-height_km / 2, height_km / 2, ny)
    X, Y = np.meshgrid(xs, ys)          # X: 동서(km), Y: 남북(km)

    # (1) 동서로 긴 타원 모양의 섬 (제주도는 약 73 km x 31 km)
    a, b = 36.5, 15.5
    r = np.sqrt((X / a) ** 2 + (Y / b) ** 2)   # 섬 중심 0 → 해안선 1
    shield = 1650.0 * np.clip(1.0 - r, 0, None) ** 1.7   # 완만한 방패 모양 화산

    # (2) 한라산 꼭대기: 가운데가 뾰족하게 솟은 봉우리
    d = np.sqrt(X ** 2 + (Y * 1.3) ** 2)
    summit = 300.0 * np.exp(-(d / 3.0) ** 2)

    # (3) 오름(작은 화산) 여러 개를 무작위로 흩뿌리기
    rng = random.Random(7)               # 같은 숫자 → 매번 같은 모양
    oreum = np.zeros_like(X)
    for _ in range(70):
        ox = rng.uniform(-a * 0.9, a * 0.9)
        oy = rng.uniform(-b * 0.85, b * 0.85)
        if (ox / a) ** 2 + (oy / b) ** 2 > 0.8:
            continue
        rad = rng.uniform(0.4, 1.1)
        oreum += rng.uniform(40, 140) * np.exp(-((X - ox) ** 2 + (Y - oy) ** 2) / rad ** 2)
    # 성산일출봉(동쪽 끝) 흉내
    oreum += 180.0 * np.exp(-((X - 35.0) ** 2 + (Y - 1.5) ** 2) / 0.8 ** 2)

    # (4) 자잘한 울퉁불퉁함
    bumps = 15.0 * np.sin(X * 1.7) * np.cos(Y * 2.3) + 10.0 * np.sin(X * 4.1 + Y * 3.3)

    heights = shield + summit + oreum + bumps
    heights = np.where(r < 1.0, heights, -50.0 - 200.0 * (r - 1.0))  # 섬 밖은 바다 밑
    log("가짜 지형 생성: 최고 높이 %.0f m" % heights.max())
    return heights, width_km, height_km


# =====================================================================
# 4. 지형 메시 만들기
# =====================================================================

def build_terrain(heights, width_km, height_km):
    """높이 배열로 격자 메시를 만들어 '제주도' 오브젝트를 돌려줍니다."""
    # 해수면보다 낮은 곳은 바닷속으로 내리기
    h = np.where(heights > SEA_LEVEL_M, heights - SEA_LEVEL_M,
                 np.minimum(heights - SEA_LEVEL_M, UNDERWATER_DEPTH_M))
    ny, nx = h.shape
    xs = np.linspace(-width_km / 2, width_km / 2, nx) * KM
    ys = np.linspace(-height_km / 2, height_km / 2, ny) * KM
    X, Y = np.meshgrid(xs, ys)
    Z = meters_to_z(h)
    verts = np.stack([X.ravel(), Y.ravel(), Z.ravel()], axis=1)

    # 격자의 네모 칸마다 면(face) 하나: 점 번호 4개
    idx = np.arange(nx * ny).reshape(ny, nx)
    faces = np.stack([idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(),
                      idx[1:, 1:].ravel(), idx[1:, :-1].ravel()], axis=1)

    mesh = bpy.data.meshes.new("Jeju_Terrain")
    mesh.from_pydata(verts.tolist(), [], faces.tolist())
    mesh.update()
    try:
        mesh.shade_smooth()            # 매끈하게 보이기
    except Exception:
        for p in mesh.polygons:
            p.use_smooth = True
    obj = bpy.data.objects.new("Jeju_Island", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(make_terrain_material(float(Z.max())))
    log("지형 메시: 점 %d개, 면 %d개" % (len(verts), len(faces)))
    return obj


def make_terrain_material(max_z):
    """높이에 따라 모래 → 초록 숲 → 갈색 → 회색 바위로 색이 바뀌는 재질."""
    mat = new_material("Terrain")
    nt = mat.node_tree
    n, l = nt.nodes, nt.links
    coord = n.new("ShaderNodeTexCoord")
    sep = n.new("ShaderNodeSeparateXYZ")
    rng = n.new("ShaderNodeMapRange")       # Z(높이)를 0~1 로 바꾸기
    rng.inputs["From Min"].default_value = 0.0
    rng.inputs["From Max"].default_value = max(max_z, 0.01)
    ramp = n.new("ShaderNodeValToRGB")      # 높이 → 색
    els = ramp.color_ramp.elements
    els[0].position, els[0].color = 0.0, (0.55, 0.50, 0.38, 1)   # 해안 모래/현무암 해변
    els[1].position, els[1].color = 0.06, (0.18, 0.32, 0.10, 1)  # 초록 들판
    for pos, col in [(0.35, (0.08, 0.20, 0.06, 1)),              # 짙은 숲
                     (0.75, (0.25, 0.20, 0.12, 1)),              # 갈색 고지대
                     (0.95, (0.45, 0.43, 0.40, 1))]:             # 회색 바위 꼭대기
        e = ramp.color_ramp.elements.new(pos)
        e.color = col
    bsdf = n.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = 0.9
    out = n.new("ShaderNodeOutputMaterial")
    l.new(coord.outputs["Object"], sep.inputs[0])
    l.new(sep.outputs["Z"], rng.inputs["Value"])
    l.new(rng.outputs["Result"], ramp.inputs["Fac"])
    l.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    l.new(bsdf.outputs[0], out.inputs["Surface"])
    return mat


# =====================================================================
# 5. 바다 (Ocean 모디파이어 + 사실적인 물 재질)
# =====================================================================

def make_water_material():
    """반사·굴절이 있는 물 재질 + 잔물결(범프) + 거품."""
    mat = new_material("Water")
    nt = mat.node_tree
    n, l = nt.nodes, nt.links

    bsdf = n.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = (0.02, 0.18, 0.22, 1)  # 물 색(굴절될 때 물든 색)
    bsdf.inputs["Roughness"].default_value = 0.03     # 거울처럼 매끈 → 햇빛 반사가 반짝
    bsdf.inputs["IOR"].default_value = 1.333          # 물의 굴절률
    set_input(bsdf, ["Transmission Weight", "Transmission"], 0.85)  # 빛이 물속으로 통과(굴절)

    # 잔물결: 4D 노이즈를 시간에 따라 움직여 표면을 자글자글하게 (반짝임이 살아남)
    coord = n.new("ShaderNodeTexCoord")
    mapping = n.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (1.0, 1.0, 1.0)
    noise = n.new("ShaderNodeTexNoise")
    noise.noise_dimensions = "4D"
    noise.inputs["Scale"].default_value = 3.0
    noise.inputs["Detail"].default_value = 8.0
    bump = n.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.25
    bump.inputs["Distance"].default_value = 0.02
    l.new(coord.outputs["Object"], mapping.inputs["Vector"])
    l.new(mapping.outputs["Vector"], noise.inputs["Vector"])
    l.new(noise.outputs["Fac"], bump.inputs["Height"])
    l.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    # 노이즈의 시간축(W)에 키프레임 → 잔물결이 계속 변함
    w = noise.inputs["W"]
    w.default_value = 0.0
    w.keyframe_insert("default_value", frame=FRAME_START)
    w.default_value = (FRAME_END - FRAME_START) / FPS * 0.4 * WAVE_SPEED
    w.keyframe_insert("default_value", frame=FRAME_END)

    # 거품: Ocean 모디파이어가 만든 'foam' 값을 읽어 하얗게 섞기
    foam = n.new("ShaderNodeAttribute")
    foam.attribute_name = "foam"
    foam_ramp = n.new("ShaderNodeValToRGB")
    foam_ramp.color_ramp.elements[0].position = 0.4
    foam_ramp.color_ramp.elements[1].position = 0.9
    foam_bsdf = n.new("ShaderNodeBsdfPrincipled")
    foam_bsdf.inputs["Base Color"].default_value = (0.9, 0.9, 0.9, 1)
    foam_bsdf.inputs["Roughness"].default_value = 0.6
    mix = n.new("ShaderNodeMixShader")
    l.new(foam.outputs["Fac"], foam_ramp.inputs["Fac"])
    l.new(foam_ramp.outputs["Color"], mix.inputs["Fac"])
    l.new(bsdf.outputs[0], mix.inputs[1])
    l.new(foam_bsdf.outputs[0], mix.inputs[2])

    out = n.new("ShaderNodeOutputMaterial")
    l.new(mix.outputs[0], out.inputs["Surface"])

    # EEVEE 에서도 굴절이 보이도록 (Cycles 에서는 무시됨)
    for attr, val in [("use_raytrace_refraction", True), ("use_screen_refraction", True)]:
        if hasattr(mat, attr):
            setattr(mat, attr, val)
    return mat


def build_ocean(water_mat):
    """Ocean 모디파이어로 물결치는 바다를 만들고, 시간(time)에 키프레임을 겁니다."""
    tile = 25.0 * KM      # 물결 한 조각 크기
    repeat = 12           # 12 x 12 조각 → 300 km x 300 km
    mesh = bpy.data.meshes.new("Ocean_Mesh")
    obj = bpy.data.objects.new("Ocean", mesh)
    bpy.context.scene.collection.objects.link(obj)
    # Ocean 모디파이어는 (원점 - 조각 반)부터 +X,+Y 방향으로 깔리므로 가운데로 옮기기
    obj.location = (-(repeat - 1) * tile / 2, -(repeat - 1) * tile / 2, 0.0)

    mod = obj.modifiers.new("Ocean", "OCEAN")
    mod.geometry_mode = "GENERATE"   # 모디파이어가 바다 평면을 직접 생성
    mod.spatial_size = int(tile)
    mod.repeat_x = repeat
    mod.repeat_y = repeat
    mod.resolution = 10              # 물결 해상도 (클수록 자세, 느림)
    mod.viewport_resolution = 6
    mod.wave_scale = 0.04            # 물결 높이 (1 단위 = 1 km 이므로 작게)
    mod.choppiness = 1.0             # 물결 끝이 뾰족해지는 정도
    mod.wind_velocity = 20.0         # 바람 세기(m/s) → 물결 크기·모양
    mod.wave_alignment = 0.3         # 물결이 한 방향으로 정렬되는 정도
    mod.wave_direction = math.radians(30)
    mod.use_normals = True
    mod.use_foam = True              # 거품 정보 만들기
    mod.foam_layer_name = "foam"
    mod.foam_coverage = 0.2

    # 시간에 키프레임: 시작 0초 → 끝 N초 (일정한 속도로)
    mod.time = 0.0
    mod.keyframe_insert("time", frame=FRAME_START)
    mod.time = (FRAME_END - FRAME_START) / FPS * WAVE_SPEED
    mod.keyframe_insert("time", frame=FRAME_END)

    mesh.materials.append(water_mat)
    try:
        mesh.shade_smooth()
    except Exception:
        pass

    # 바다 조각 바깥(지평선까지)을 덮는 아주 큰 평평한 물
    far = make_plane("Ocean_Far", size=6000.0 * KM, z=-0.01 * KM)
    far.data.materials.append(water_mat)
    # 물속이 비어 보이지 않도록 깊은 바닥
    floor = make_plane("Sea_Floor", size=6000.0 * KM, z=meters_to_z(UNDERWATER_DEPTH_M) - 0.05 * KM)
    floor_mat = new_material("Sea_Floor")
    fb = floor_mat.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
    fb.inputs["Base Color"].default_value = (0.01, 0.05, 0.08, 1)
    fo = floor_mat.node_tree.nodes.new("ShaderNodeOutputMaterial")
    floor_mat.node_tree.links.new(fb.outputs[0], fo.inputs["Surface"])
    floor.data.materials.append(floor_mat)
    log("바다 생성: %d x %d km, 물결 애니메이션 %.1f초" % (tile * repeat, tile * repeat, mod.time))
    return obj


def make_plane(name, size, z):
    """가운데가 원점인 정사각형 평면 하나."""
    s = size / 2
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(-s, -s, 0), (s, -s, 0), (s, s, 0), (-s, s, 0)], [], [(0, 1, 2, 3)])
    obj = bpy.data.objects.new(name, mesh)
    obj.location.z = z
    bpy.context.scene.collection.objects.link(obj)
    return obj


# =====================================================================
# 6. 하늘과 태양
# =====================================================================

def sun_angles(t):
    """t = 0(해 뜰 때) ~ 1(해 질 때) → (방위각, 고도) [라디안].
    방위각: 0 = 북, 90° = 동, 180° = 남, 270° = 서."""
    azimuth = math.radians(90 + 180 * t)   # 동 → 남 → 서
    elev = SUN_MIN_ELEVATION_DEG + (SUN_MAX_ELEVATION_DEG - SUN_MIN_ELEVATION_DEG) * math.sin(math.pi * t)
    return azimuth, math.radians(elev)


def sun_vector(azimuth, elev):
    """방위각·고도 → 태양 쪽을 가리키는 방향 벡터 (+X 동, +Y 북)."""
    return Vector((math.sin(azimuth) * math.cos(elev),
                   math.cos(azimuth) * math.cos(elev),
                   math.sin(elev)))


def build_sky_and_sun():
    scene = bpy.context.scene

    # --- 하늘 (World) ---
    world = bpy.data.worlds.new("Jeju_Sky")
    scene.world = world
    if bpy.app.version < (5, 0, 0):
        world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    sky = nt.nodes.new("ShaderNodeTexSky")
    # 버전에 따라 이름이 다름: 5.x = MULTIPLE_SCATTERING, 4.x = NISHITA
    kinds = [i.identifier for i in sky.bl_rna.properties["sky_type"].enum_items]
    for k in ("MULTIPLE_SCATTERING", "SINGLE_SCATTERING", "NISHITA"):
        if k in kinds:
            sky.sky_type = k
            break
    sky.sun_disc = True           # 하늘에 동그란 해가 보이게
    sky.sun_size = math.radians(0.545)
    sky.sun_intensity = 0.3
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Strength"].default_value = 0.15  # 하늘 전체 밝기
    out = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(sky.outputs["Color"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])

    # --- 태양 (Sun 라이트): 그림자와 강한 햇빛 반사를 담당 ---
    light = bpy.data.lights.new("Sun", type="SUN")
    light.angle = math.radians(0.53)   # 실제 해의 크기 → 또렷한 그림자·반사
    sun = bpy.data.objects.new("Sun", light)
    scene.collection.objects.link(sun)

    # --- 프레임마다(몇 프레임 간격) 태양 위치에 키프레임 ---
    step = 4
    prev_euler = None
    frames = list(range(FRAME_START, FRAME_END + 1, step))
    if frames[-1] != FRAME_END:
        frames.append(FRAME_END)
    for f in frames:
        t = (f - FRAME_START) / max(1, FRAME_END - FRAME_START)
        az, el = sun_angles(t)

        # 하늘 속 해의 위치 (sun_rotation 0 = 북쪽(+Y), 90° = 동쪽(+X))
        sky.sun_elevation = el
        sky.sun_rotation = az
        sky.keyframe_insert("sun_elevation", frame=f)
        sky.keyframe_insert("sun_rotation", frame=f)

        # Sun 라이트는 자기 -Z 방향으로 빛을 쏨 → +Z 축을 해 쪽으로 돌리기
        quat = sun_vector(az, el).to_track_quat("Z", "Y")
        euler = quat.to_euler("XYZ", prev_euler) if prev_euler else quat.to_euler("XYZ")
        sun.rotation_euler = euler
        prev_euler = euler
        sun.keyframe_insert("rotation_euler", frame=f)

        # 해가 낮으면 주황빛·약하게, 높으면 흰빛·강하게
        k = min(1.0, math.degrees(el) / 25.0)
        light.color = (1.0, 0.55 + 0.42 * k, 0.30 + 0.62 * k)
        light.energy = 1.0 + 1.5 * k
        light.keyframe_insert("color", frame=f)
        light.keyframe_insert("energy", frame=f)
    log("하늘·태양 생성: 태양 고도 %.0f° → %.0f° → %.0f°, 동 → 남 → 서"
        % (SUN_MIN_ELEVATION_DEG, SUN_MAX_ELEVATION_DEG, SUN_MIN_ELEVATION_DEG))
    return sun


# =====================================================================
# 7. 카메라와 렌더 설정
# =====================================================================

def build_camera(island_top_z):
    """섬 북쪽 하늘에서 남쪽을 내려다보는 카메라.
    태양이 남쪽 하늘을 지나가므로, 카메라 앞바다와 섬 너머 바다에 햇빛 반사가 생깁니다."""
    scene = bpy.context.scene
    cam_data = bpy.data.cameras.new("Camera")
    cam_data.lens = 24                  # 광각 렌즈 (섬 전체가 들어오게)
    cam_data.clip_start = 0.05 * KM
    cam_data.clip_end = 8000.0 * KM
    cam = bpy.data.objects.new("Camera", cam_data)
    scene.collection.objects.link(cam)
    cam.location = (0.0, 62.0 * KM, 13.0 * KM)  # 섬 북쪽 62 km, 높이 13 km 상공
    target = Vector((0.0, -2.0 * KM, island_top_z * 0.3))  # 섬 가운데쯤을 바라봄
    direction = target - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam
    return cam


def setup_render():
    scene = bpy.context.scene
    scene.frame_start = FRAME_START
    scene.frame_end = FRAME_END
    scene.frame_current = FRAME_START + (FRAME_END - FRAME_START) // 2
    scene.render.fps = FPS
    scene.render.resolution_x = RES_X
    scene.render.resolution_y = RES_Y
    scene.render.resolution_percentage = RES_PERCENT

    if RENDER_ENGINE.upper() == "EEVEE":
        for name in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):   # 버전마다 이름이 다름
            try:
                scene.render.engine = name
                break
            except TypeError:
                continue
        ee = scene.eevee
        for attr, val in [("use_raytracing", True), ("use_ssr", True),
                          ("use_ssr_refraction", True), ("taa_render_samples", 64)]:
            if hasattr(ee, attr):
                setattr(ee, attr, val)
    else:
        scene.render.engine = "CYCLES"
        scene.cycles.samples = CYCLES_SAMPLES
        scene.cycles.use_denoising = True    # 노이즈(자글자글함) 제거
        scene.cycles.max_bounces = 8
        scene.cycles.transmission_bounces = 8
        scene.cycles.caustics_reflective = False  # 느려지는 효과 끄기
        scene.cycles.caustics_refractive = False

    # 색 표현: AgX (밝은 햇빛도 자연스럽게)
    try:
        scene.view_settings.view_transform = "AgX"
    except TypeError:
        scene.view_settings.view_transform = "Filmic"
    scene.view_settings.exposure = -1.0   # 조금 어둡게 (햇빛이 강해서)

    # 출력 파일
    scene.render.filepath = OUTPUT_PATH
    done = False
    if OUTPUT_AS_VIDEO:
        try:
            ims = scene.render.image_settings
            if hasattr(ims, "media_type"):       # Blender 5.x
                ims.media_type = "VIDEO"
            ims.file_format = "FFMPEG"
            scene.render.ffmpeg.format = "MPEG4"
            scene.render.ffmpeg.codec = "H264"
            scene.render.ffmpeg.constant_rate_factor = "HIGH"
            done = True
        except Exception as e:
            log("동영상 출력 설정 실패 → PNG 로 저장합니다 (%s)" % e)
    if not done:
        ims = scene.render.image_settings
        if hasattr(ims, "media_type"):
            ims.media_type = "IMAGE"
        ims.file_format = "PNG"
    log("렌더 설정: %s, %dx%d (%d%%), 프레임 %d~%d"
        % (scene.render.engine, RES_X, RES_Y, RES_PERCENT, FRAME_START, FRAME_END))


# =====================================================================
# 8. 실행
# =====================================================================

def main():
    log("시작합니다. Blender " + bpy.app.version_string)
    if CLEAR_SCENE:
        clear_scene()

    # 새로 넣는 키프레임이 '일정한 속도(LINEAR)'가 되도록 잠시 설정 변경 (끝나면 원래대로)
    prefs = bpy.context.preferences.edit
    old_interp = prefs.keyframe_new_interpolation_type
    prefs.keyframe_new_interpolation_type = "LINEAR"
    try:
        # 지형
        heights = None
        if DEM_PATH and os.path.isfile(DEM_PATH):
            try:
                heights, w_km, h_km = load_dem(DEM_PATH)
            except Exception as e:
                log("DEM 을 읽지 못했습니다 (%s) → 가짜 지형으로 대신합니다." % e)
        elif DEM_PATH:
            log("DEM 파일이 없습니다: %s → 가짜 지형으로 대신합니다." % DEM_PATH)
        else:
            log("DEM_PATH 가 비어 있음 → 가짜 지형(한라산 흉내)으로 실행합니다.")
        if heights is None:
            heights, w_km, h_km = fake_jeju_heights()
        island = build_terrain(heights, w_km, h_km)

        # 바다, 하늘·태양, 카메라, 렌더
        build_ocean(make_water_material())
        build_sky_and_sun()
        build_camera(max(v.co.z for v in island.data.vertices))
        setup_render()
    finally:
        prefs.keyframe_new_interpolation_type = old_interp

    log("완료! 스페이스바로 애니메이션 재생, F12 로 렌더해 보세요.")


main()
