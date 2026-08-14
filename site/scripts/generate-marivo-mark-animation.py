from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from PIL import Image, ImageDraw

Vec3: TypeAlias = tuple[float, float, float]
Matrix3: TypeAlias = tuple[Vec3, Vec3, Vec3]
Color: TypeAlias = tuple[int, int, int, int]
Point2: TypeAlias = tuple[float, float]

SIZE = 512
GIF_SIZE = 320
GIF_COLOR_COUNT = 63
GIF_TRANSPARENT_INDEX = 63
SUPERSAMPLE = 2
FRAME_DURATION_MS = 40
TURN_FRAMES_SCRAMBLE = 6
TURN_FRAMES_SOLVE = 10

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_WEBP = ROOT / "src" / "assets" / "marivo-mark-animated.webp"
OUTPUT_GIF = ROOT / "src" / "assets" / "marivo-mark-animated.gif"

IDENTITY: Matrix3 = (
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 0.0, 1.0),
)

AXES: tuple[Vec3, ...] = (
    (1.0, 0.0, 0.0),
    (-1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, -1.0, 0.0),
    (0.0, 0.0, 1.0),
    (0.0, 0.0, -1.0),
)

FACE_BASIS: dict[Vec3, tuple[Vec3, Vec3]] = {
    (1.0, 0.0, 0.0): ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
    (-1.0, 0.0, 0.0): ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    (0.0, 1.0, 0.0): ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0)),
    (0.0, -1.0, 0.0): ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    (0.0, 0.0, 1.0): ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    (0.0, 0.0, -1.0): ((-1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
}

FACE_COLORS: dict[Vec3, str] = {
    (1.0, 0.0, 0.0): "#679486",
    (-1.0, 0.0, 0.0): "#6fbca5",
    (0.0, 1.0, 0.0): "#c3f0e1",
    (0.0, -1.0, 0.0): "#efe8c8",
    (0.0, 0.0, 1.0): "#14815e",
    (0.0, 0.0, -1.0): "#2f7168",
}

BODY_COLOR = "#114433"
BRAND_COLOR = "#ffffee"
CAMERA = (4.8, 4.0, 7.5)
LIGHT = (-0.25, 1.0, 0.9)
BODY_HALF = 0.485
STICKER_HALF = 0.405
STICKER_RADIUS = 0.075
STICKER_OFFSET = BODY_HALF + 0.009

M_POLYGONS: tuple[tuple[Point2, ...], ...] = (
    ((-1.10, 0.96), (-0.58, 0.96), (-0.58, -1.14), (-1.10, -1.14)),
    ((0.58, 0.94), (1.10, 0.94), (1.10, -1.14), (0.58, -1.14)),
    ((-0.65, 0.96), (0.0, 0.04), (0.58, 0.94), (0.58, 0.18), (0.0, -0.92), (-0.65, 0.18)),
)


@dataclass
class Sticker:
    normal: Vec3
    color: str
    brand_polygons: list[list[Point2]]


@dataclass
class Cubie:
    position: Vec3
    orientation: Matrix3
    stickers: list[Sticker]


@dataclass(frozen=True)
class Move:
    axis: int
    layer: int
    quarter_turns: int

    def inverse(self) -> Move:
        return Move(self.axis, self.layer, -self.quarter_turns)


@dataclass
class Polygon:
    points: list[tuple[float, float]]
    depth: float
    fill: Color


def add(left: Vec3, right: Vec3) -> Vec3:
    return (left[0] + right[0], left[1] + right[1], left[2] + right[2])


def scale(vector: Vec3, factor: float) -> Vec3:
    return (vector[0] * factor, vector[1] * factor, vector[2] * factor)


def dot(left: Vec3, right: Vec3) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def cross(left: Vec3, right: Vec3) -> Vec3:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def normalize(vector: Vec3) -> Vec3:
    length = math.sqrt(dot(vector, vector))
    return scale(vector, 1.0 / length)


def mat_vec(matrix: Matrix3, vector: Vec3) -> Vec3:
    return (
        dot(matrix[0], vector),
        dot(matrix[1], vector),
        dot(matrix[2], vector),
    )


def mat_mul(left: Matrix3, right: Matrix3) -> Matrix3:
    columns: Matrix3 = (
        (right[0][0], right[1][0], right[2][0]),
        (right[0][1], right[1][1], right[2][1]),
        (right[0][2], right[1][2], right[2][2]),
    )
    return (
        (dot(left[0], columns[0]), dot(left[0], columns[1]), dot(left[0], columns[2])),
        (dot(left[1], columns[0]), dot(left[1], columns[1]), dot(left[1], columns[2])),
        (dot(left[2], columns[0]), dot(left[2], columns[1]), dot(left[2], columns[2])),
    )


def rotation(axis: int, angle: float) -> Matrix3:
    sine = math.sin(angle)
    cosine = math.cos(angle)
    if axis == 0:
        return ((1.0, 0.0, 0.0), (0.0, cosine, -sine), (0.0, sine, cosine))
    if axis == 1:
        return ((cosine, 0.0, sine), (0.0, 1.0, 0.0), (-sine, 0.0, cosine))
    return ((cosine, -sine, 0.0), (sine, cosine, 0.0), (0.0, 0.0, 1.0))


def rounded_face_points(
    normal: Vec3,
    half_extent: float,
    radius: float,
    offset: float,
) -> list[Vec3]:
    basis_u, basis_v = FACE_BASIS[normal]
    points: list[Vec3] = []
    arc_specs = (
        (half_extent - radius, half_extent - radius, 0.0, 90.0),
        (-half_extent + radius, half_extent - radius, 90.0, 180.0),
        (-half_extent + radius, -half_extent + radius, 180.0, 270.0),
        (half_extent - radius, -half_extent + radius, 270.0, 360.0),
    )
    for center_u, center_v, start, end in arc_specs:
        for index in range(5):
            angle = math.radians(start + (end - start) * index / 4)
            coordinate_u = center_u + radius * math.cos(angle)
            coordinate_v = center_v + radius * math.sin(angle)
            point = add(
                scale(normal, offset),
                add(scale(basis_u, coordinate_u), scale(basis_v, coordinate_v)),
            )
            points.append(point)
    return points


def square_face_points(normal: Vec3, half_extent: float) -> list[Vec3]:
    basis_u, basis_v = FACE_BASIS[normal]
    center = scale(normal, half_extent)
    return [
        add(center, add(scale(basis_u, u), scale(basis_v, v)))
        for u, v in (
            (-half_extent, -half_extent),
            (half_extent, -half_extent),
            (half_extent, half_extent),
            (-half_extent, half_extent),
        )
    ]


def face_plane_points(normal: Vec3, points: list[Point2], offset: float) -> list[Vec3]:
    basis_u, basis_v = FACE_BASIS[normal]
    return [
        add(
            scale(normal, offset),
            add(scale(basis_u, point[0]), scale(basis_v, point[1])),
        )
        for point in points
    ]


def clip_polygon(
    points: list[Point2],
    axis: int,
    boundary: float,
    keep_greater: bool,
) -> list[Point2]:
    if not points:
        return []

    def inside(point: Point2) -> bool:
        if keep_greater:
            return point[axis] >= boundary
        return point[axis] <= boundary

    def intersection(start: Point2, end: Point2) -> Point2:
        delta = end[axis] - start[axis]
        if abs(delta) < 1e-12:
            return start
        amount = (boundary - start[axis]) / delta
        return (
            start[0] + (end[0] - start[0]) * amount,
            start[1] + (end[1] - start[1]) * amount,
        )

    clipped: list[Point2] = []
    previous = points[-1]
    previous_inside = inside(previous)
    for current in points:
        current_inside = inside(current)
        if current_inside:
            if not previous_inside:
                clipped.append(intersection(previous, current))
            clipped.append(current)
        elif previous_inside:
            clipped.append(intersection(previous, current))
        previous = current
        previous_inside = current_inside
    return clipped


def brand_fragments(cubie_x: int, cubie_y: int) -> list[list[Point2]]:
    minimum_u = cubie_x - STICKER_HALF
    maximum_u = cubie_x + STICKER_HALF
    minimum_v = cubie_y - STICKER_HALF
    maximum_v = cubie_y + STICKER_HALF
    fragments: list[list[Point2]] = []
    for source_polygon in M_POLYGONS:
        clipped = list(source_polygon)
        clipped = clip_polygon(clipped, axis=0, boundary=minimum_u, keep_greater=True)
        clipped = clip_polygon(clipped, axis=0, boundary=maximum_u, keep_greater=False)
        clipped = clip_polygon(clipped, axis=1, boundary=minimum_v, keep_greater=True)
        clipped = clip_polygon(clipped, axis=1, boundary=maximum_v, keep_greater=False)
        if len(clipped) < 3:
            continue
        fragments.append([(point[0] - cubie_x, point[1] - cubie_y) for point in clipped])
    return fragments


def parse_color(value: str, factor: float = 1.0) -> Color:
    value = value.removeprefix("#")
    channels = [int(value[index : index + 2], 16) for index in (0, 2, 4)]
    shaded = tuple(max(0, min(255, round(channel * factor))) for channel in channels)
    return (shaded[0], shaded[1], shaded[2], 255)


CAMERA_DIRECTION = normalize(CAMERA)
SCREEN_RIGHT = normalize(cross((0.0, 1.0, 0.0), CAMERA_DIRECTION))
SCREEN_UP = normalize(cross(CAMERA_DIRECTION, SCREEN_RIGHT))
LIGHT_DIRECTION = normalize(LIGHT)


def project(point: Vec3, canvas_size: int) -> tuple[float, float]:
    projection_scale = canvas_size * 0.153
    center_x = canvas_size * 0.5
    center_y = canvas_size * 0.49
    return (
        center_x + dot(point, SCREEN_RIGHT) * projection_scale,
        center_y - dot(point, SCREEN_UP) * projection_scale,
    )


def shade(color: str, normal: Vec3) -> Color:
    light = max(0.0, dot(normal, LIGHT_DIRECTION))
    return parse_color(color, 0.86 + 0.16 * light)


def transformed_cubie(
    cubie: Cubie,
    active_ids: set[int],
    cubie_id: int,
    active_rotation: Matrix3,
) -> tuple[Vec3, Matrix3]:
    if cubie_id not in active_ids:
        return cubie.position, cubie.orientation
    return (
        mat_vec(active_rotation, cubie.position),
        mat_mul(active_rotation, cubie.orientation),
    )


def transform_points(points: list[Vec3], position: Vec3, orientation: Matrix3) -> list[Vec3]:
    return [add(mat_vec(orientation, point), position) for point in points]


def polygon_from_world(
    world_points: list[Vec3],
    normal: Vec3,
    fill: str,
    canvas_size: int,
) -> Polygon:
    return Polygon(
        points=[project(point, canvas_size) for point in world_points],
        depth=sum(dot(point, CAMERA_DIRECTION) for point in world_points) / len(world_points),
        fill=shade(fill, normal),
    )


def build_cubies() -> list[Cubie]:
    cubies: list[Cubie] = []
    for x in (-1, 0, 1):
        for y in (-1, 0, 1):
            for z in (-1, 0, 1):
                stickers: list[Sticker] = []
                coordinates = (x, y, z)
                for axis in range(3):
                    for side in (-1, 1):
                        if coordinates[axis] != side:
                            continue
                        normal: Vec3
                        if axis == 0:
                            normal = (float(side), 0.0, 0.0)
                        elif axis == 1:
                            normal = (0.0, float(side), 0.0)
                        else:
                            normal = (0.0, 0.0, float(side))
                        brand = brand_fragments(x, y) if normal == (0.0, 0.0, 1.0) else []
                        stickers.append(Sticker(normal, FACE_COLORS[normal], brand))
                cubies.append(
                    Cubie(
                        position=(float(x), float(y), float(z)),
                        orientation=IDENTITY,
                        stickers=stickers,
                    )
                )
    return cubies


def add_core_polygons(polygons: list[Polygon], canvas_size: int) -> None:
    core_half = 1.445
    for normal in AXES:
        if dot(normal, CAMERA_DIRECTION) <= 0.0:
            continue
        points = square_face_points(normal, core_half)
        polygons.append(polygon_from_world(points, normal, BODY_COLOR, canvas_size))


def render_cube(
    cubies: list[Cubie],
    active_ids: set[int] | None = None,
    active_rotation: Matrix3 = IDENTITY,
) -> Image.Image:
    canvas_size = SIZE * SUPERSAMPLE
    active_ids = active_ids or set()
    core_polygons: list[Polygon] = []
    polygons: list[Polygon] = []
    add_core_polygons(core_polygons, canvas_size)

    for cubie_id, cubie in enumerate(cubies):
        position, orientation = transformed_cubie(cubie, active_ids, cubie_id, active_rotation)
        for local_normal in AXES:
            world_normal = mat_vec(orientation, local_normal)
            if dot(world_normal, CAMERA_DIRECTION) <= 0.01:
                continue
            local_points = square_face_points(local_normal, BODY_HALF)
            world_points = transform_points(local_points, position, orientation)
            polygons.append(polygon_from_world(world_points, world_normal, BODY_COLOR, canvas_size))

        for sticker in cubie.stickers:
            world_normal = mat_vec(orientation, sticker.normal)
            if dot(world_normal, CAMERA_DIRECTION) <= 0.01:
                continue
            local_points = rounded_face_points(
                sticker.normal,
                STICKER_HALF,
                STICKER_RADIUS,
                STICKER_OFFSET,
            )
            world_points = transform_points(local_points, position, orientation)
            sticker_polygon = polygon_from_world(
                world_points,
                world_normal,
                sticker.color,
                canvas_size,
            )
            polygons.append(sticker_polygon)
            for brand_index, brand_polygon in enumerate(sticker.brand_polygons):
                local_brand_points = face_plane_points(
                    sticker.normal,
                    brand_polygon,
                    STICKER_OFFSET + 0.004,
                )
                world_brand_points = transform_points(
                    local_brand_points,
                    position,
                    orientation,
                )
                rendered_brand = polygon_from_world(
                    world_brand_points,
                    world_normal,
                    BRAND_COLOR,
                    canvas_size,
                )
                rendered_brand.depth = sticker_polygon.depth + 0.0001 * (brand_index + 1)
                polygons.append(rendered_brand)

    image = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for polygon in sorted(core_polygons, key=lambda item: item.depth):
        draw.polygon(polygon.points, fill=polygon.fill)
    for polygon in sorted(polygons, key=lambda item: item.depth):
        draw.polygon(polygon.points, fill=polygon.fill)
    return image.resize((SIZE, SIZE), Image.Resampling.LANCZOS)


def ease(value: float) -> float:
    return value * value * (3.0 - 2.0 * value)


def append_turn(
    frames: list[Image.Image],
    cubies: list[Cubie],
    move: Move,
    frame_count: int,
) -> None:
    active_ids = {
        index
        for index, cubie in enumerate(cubies)
        if round(cubie.position[move.axis]) == move.layer
    }
    final_angle = move.quarter_turns * math.pi / 2.0
    for index in range(1, frame_count + 1):
        angle = final_angle * ease(index / frame_count)
        frames.append(render_cube(cubies, active_ids, rotation(move.axis, angle)))

    committed_rotation = rotation(move.axis, final_angle)
    for cubie_id in active_ids:
        cubie = cubies[cubie_id]
        rotated_position = mat_vec(committed_rotation, cubie.position)
        cubie.position = (
            float(round(rotated_position[0])),
            float(round(rotated_position[1])),
            float(round(rotated_position[2])),
        )
        cubie.orientation = mat_mul(committed_rotation, cubie.orientation)


def make_gif_frames(frames: list[Image.Image]) -> list[Image.Image]:
    gif_frames: list[Image.Image] = []
    for frame in frames:
        gif_frame = frame.resize((GIF_SIZE, GIF_SIZE), Image.Resampling.LANCZOS)
        palette_frame = gif_frame.convert("RGB").quantize(
            colors=GIF_COLOR_COUNT,
            method=Image.Quantize.MEDIANCUT,
        )
        transparent_mask = gif_frame.getchannel("A").point(lambda alpha: 255 if alpha < 128 else 0)
        palette_frame.paste(GIF_TRANSPARENT_INDEX, mask=transparent_mask)
        palette = palette_frame.getpalette()
        if palette is not None:
            palette[GIF_TRANSPARENT_INDEX * 3 : GIF_TRANSPARENT_INDEX * 3 + 3] = [0, 0, 0]
            palette_frame.putpalette(palette)
        palette_frame.info["transparency"] = GIF_TRANSPARENT_INDEX
        gif_frames.append(palette_frame)
    return gif_frames


def validate_solved(cubies: list[Cubie]) -> None:
    expected_positions = sorted(
        (float(x), float(y), float(z)) for x in (-1, 0, 1) for y in (-1, 0, 1) for z in (-1, 0, 1)
    )
    actual_positions = sorted(cubie.position for cubie in cubies)
    if actual_positions != expected_positions:
        raise RuntimeError("The inverse move sequence did not restore all cubie positions.")
    for cubie in cubies:
        for row, expected_row in zip(cubie.orientation, IDENTITY, strict=True):
            if any(
                abs(value - expected) > 1e-8
                for value, expected in zip(row, expected_row, strict=True)
            ):
                raise RuntimeError(
                    "The inverse move sequence did not restore all cubie orientations."
                )


def main() -> None:
    cubies = build_cubies()
    solved_cube = render_cube(cubies)

    scramble = (
        Move(axis=0, layer=1, quarter_turns=-1),
        Move(axis=1, layer=1, quarter_turns=-1),
        Move(axis=2, layer=1, quarter_turns=-1),
        Move(axis=0, layer=1, quarter_turns=1),
    )
    restore = tuple(move.inverse() for move in reversed(scramble))

    frames: list[Image.Image] = [solved_cube.copy() for _ in range(10)]
    for move in scramble:
        append_turn(frames, cubies, move, TURN_FRAMES_SCRAMBLE)
    frames.extend(render_cube(cubies) for _ in range(3))
    for move in restore:
        append_turn(frames, cubies, move, TURN_FRAMES_SOLVE)
    validate_solved(cubies)
    restored_cube = render_cube(cubies)
    frames.extend(restored_cube.copy() for _ in range(14))

    frames[0].save(
        OUTPUT_WEBP,
        save_all=True,
        append_images=frames[1:],
        duration=FRAME_DURATION_MS,
        loop=0,
        lossless=False,
        quality=86,
        method=4,
    )

    gif_frames = make_gif_frames(frames)
    gif_frames[0].save(
        OUTPUT_GIF,
        save_all=True,
        append_images=gif_frames[1:],
        duration=FRAME_DURATION_MS,
        loop=0,
        transparency=GIF_TRANSPARENT_INDEX,
        disposal=2,
        optimize=True,
    )
    print(f"Generated {len(frames)} frames at {1000 // FRAME_DURATION_MS} fps")
    print(OUTPUT_WEBP)
    print(OUTPUT_GIF)


if __name__ == "__main__":
    main()
