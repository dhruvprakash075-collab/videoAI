from typing import Any

class RealESRGANer:
    def __init__(
        self,
        *,
        scale: int,
        model_path: str,
        model: Any,
        tile: int,
        tile_pad: int,
        pre_pad: int,
        half: bool,
    ) -> None: ...

    def enhance(self, img: Any, *, outscale: int) -> tuple[Any, str]: ...
