class RRDBNet:
    def __init__(
        self,
        *,
        num_in_ch: int,
        num_out_ch: int,
        num_feat: int,
        num_block: int,
        num_grow_ch: int,
        scale: int,
    ) -> None: ...
