# Minimal local re-implementation of Meta's DINOv2 (facebookresearch/dinov2,
# Apache-2.0)：ViT-L/14 与 ViT-B/14 两种规模，直接加载官方 checkpoint。
#
# 来源：ComfyUI-VOSR2 节点的 models/dinov2.py（ylchen333, Apache-2.0），
# 该文件本身是 Meta DINOv2 的本地复刻。VOSR2-Lite 适配：移除 comfy.ops
# 依赖（ComfyUI 专用），改用标准 nn 模块（state_dict 键与数值不受影响）。
#
# 为何不直接用 torch.hub.load：hub 路径会在 import/执行期拉取并执行缓存仓库
# 代码（facebookresearch_dinov2_main），既有供应链风险也要求随仓库克隆代码；
# 本地复刻后只需权重文件，且 forward_intermediate_layer 可跳过无用 block。
import math

import torch
import torch.nn as nn
import torch.nn.functional as F

DINOV2_VITL14_CONFIG = dict(
    img_size=518,
    patch_size=14,
    embed_dim=1024,
    depth=24,
    num_heads=16,
    mlp_ratio=4.0,
)

# ViT-B/14：0.5B 系检查点（VOSR_0.5B_os / _ms）使用，权重 dinov2_vitb14_pretrain.pth
DINOV2_VITB14_CONFIG = dict(
    img_size=518,
    patch_size=14,
    embed_dim=768,
    depth=12,
    num_heads=12,
    mlp_ratio=4.0,
)


class PatchEmbed(nn.Module):
    def __init__(self, img_size, patch_size, embed_dim):
        super().__init__()
        self.patch_size = patch_size
        self.proj = nn.Conv2d(3, embed_dim, kernel_size=patch_size, stride=patch_size)

    def forward(self, x):
        x = self.proj(x)
        return x.flatten(2).transpose(1, 2)


class LayerScale(nn.Module):
    def __init__(self, dim, init_values: float = 1.0):
        super().__init__()
        self.gamma = nn.Parameter(init_values * torch.ones(dim))

    def forward(self, x):
        return x * self.gamma


class Attention(nn.Module):
    def __init__(self, dim, num_heads):
        super().__init__()
        self.num_heads = num_heads
        self.head_dim = dim // num_heads
        self.qkv = nn.Linear(dim, dim * 3, bias=True)
        self.proj = nn.Linear(dim, dim, bias=True)

    def forward(self, x):
        B, N, C = x.shape
        qkv = self.qkv(x).reshape(B, N, 3, self.num_heads, self.head_dim).permute(2, 0, 3, 1, 4)
        q, k, v = qkv.unbind(0)
        x = F.scaled_dot_product_attention(q, k, v)
        x = x.transpose(1, 2).reshape(B, N, C)
        return self.proj(x)


class Mlp(nn.Module):
    def __init__(self, dim, hidden_dim):
        super().__init__()
        self.fc1 = nn.Linear(dim, hidden_dim)
        self.act = nn.GELU()
        self.fc2 = nn.Linear(hidden_dim, dim)

    def forward(self, x):
        return self.fc2(self.act(self.fc1(x)))


class Block(nn.Module):
    def __init__(self, dim, num_heads, mlp_ratio):
        super().__init__()
        self.norm1 = nn.LayerNorm(dim, eps=1e-6)
        self.attn = Attention(dim, num_heads)
        self.ls1 = LayerScale(dim)
        self.norm2 = nn.LayerNorm(dim, eps=1e-6)
        self.mlp = Mlp(dim, int(dim * mlp_ratio))
        self.ls2 = LayerScale(dim)

    def forward(self, x):
        x = x + self.ls1(self.attn(self.norm1(x)))
        x = x + self.ls2(self.mlp(self.norm2(x)))
        return x


class DinoVisionTransformer(nn.Module):
    def __init__(self, img_size=518, patch_size=14, embed_dim=1024, depth=24, num_heads=16, mlp_ratio=4.0):
        super().__init__()
        self.patch_size = patch_size
        self.embed_dim = embed_dim
        self.patch_embed = PatchEmbed(img_size, patch_size, embed_dim)
        num_patches = (img_size // patch_size) ** 2

        self.cls_token = nn.Parameter(torch.zeros(1, 1, embed_dim))
        self.pos_embed = nn.Parameter(torch.zeros(1, num_patches + 1, embed_dim))
        self.mask_token = nn.Parameter(torch.zeros(1, embed_dim))

        self.blocks = nn.ModuleList([Block(embed_dim, num_heads, mlp_ratio) for _ in range(depth)])
        self.norm = nn.LayerNorm(embed_dim, eps=1e-6)

    def interpolate_pos_encoding(self, x, w, h):
        """Vendored verbatim from facebookresearch/dinov2 (Apache-2.0): bicubic-resizes
        the patch position embeddings to the current (w, h) grid. The `+ 0.1` offset
        before the target size and the `int()` truncation after are load-bearing --
        they sidestep a floating-point rounding edge case in `F.interpolate`'s
        `scale_factor` path that the upstream implementation works around this way."""
        previous_dtype = x.dtype
        npatch = x.shape[1] - 1
        N = self.pos_embed.shape[1] - 1
        if npatch == N and w == h:
            return self.pos_embed
        pos_embed = self.pos_embed.float()
        class_pos_embed = pos_embed[:, 0]
        patch_pos_embed = pos_embed[:, 1:]
        dim = x.shape[-1]
        w0 = w // self.patch_size
        h0 = h // self.patch_size
        w0, h0 = w0 + 0.1, h0 + 0.1
        patch_pos_embed = F.interpolate(
            patch_pos_embed.reshape(1, int(math.sqrt(N)), int(math.sqrt(N)), dim).permute(0, 3, 1, 2),
            scale_factor=(w0 / math.sqrt(N), h0 / math.sqrt(N)),
            mode="bicubic",
        )
        assert int(w0) == patch_pos_embed.shape[-2] and int(h0) == patch_pos_embed.shape[-1]
        patch_pos_embed = patch_pos_embed.permute(0, 2, 3, 1).view(1, -1, dim)
        return torch.cat((class_pos_embed.unsqueeze(0), patch_pos_embed), dim=1).to(previous_dtype)

    def prepare_tokens(self, x):
        B, _, w, h = x.shape
        x = self.patch_embed(x)
        cls_tokens = self.cls_token.expand(B, -1, -1)
        x = torch.cat((cls_tokens, x), dim=1)
        x = x + self.interpolate_pos_encoding(x, w, h)
        return x

    def forward_intermediate_layer(self, x: torch.Tensor, layer_idx: int) -> torch.Tensor:
        """Run through `layer_idx + 1` blocks and return patch tokens (cls token dropped).

        VOSR2 only ever needs one fixed intermediate layer's patch tokens
        (`layer_dinov2b_list == [17]`, validated by the loader), so the remaining
        `depth - layer_idx - 1` blocks are skipped rather than computed and discarded.
        """
        x = self.prepare_tokens(x)
        for block in self.blocks[: layer_idx + 1]:
            x = block(x)
        return x[:, 1:]

    def forward_with_features(self, x, masks=None):
        """与上游 torch.hub 版 DINOv2 的特征接口保持一致（逐层收集 + 末层 norm），
        供 get_venc_features 按 args.layer_dinov2b_list 选取。"""
        features = {}
        x = self.prepare_tokens(x)
        for i, blk in enumerate(self.blocks):
            x = blk(x)
            features[f"layer_{i}"] = x[:, 1:]
        x_norm = self.norm(x)
        return features, x_norm[:, 1:]


def build_dinov2_vitl14() -> DinoVisionTransformer:
    return DinoVisionTransformer(**DINOV2_VITL14_CONFIG)


def build_dinov2_vitb14() -> DinoVisionTransformer:
    return DinoVisionTransformer(**DINOV2_VITB14_CONFIG)


# enc_type（上游 args.json）-> (构建函数, 权重文件名, 中间层特征维度)
DINOV2_VARIANTS = {
    "dinov2l": (build_dinov2_vitl14, "dinov2_vitl14_pretrain.pth", 1024),
    "dinov2b": (build_dinov2_vitb14, "dinov2_vitb14_pretrain.pth", 768),
}
