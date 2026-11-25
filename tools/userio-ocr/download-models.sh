#!/bin/sh
set -eu
dest="$1"
mkdir -p "$dest"
while read -r sha name; do
    curl -fsSL --retry 2 "https://www.modelscope.cn/api/v1/models/greatv/oar-ocr/repo?Revision=master&FilePath=$name" -o "$dest/$name"
    printf '%s  %s\n' "$sha" "$dest/$name" | sha256sum -c -
done <<'EOF'
193bab7a04fca699a6c82e6abb5b81bdb28177f0abd4062552b04908dafb19f8 pp-ocrv6_tiny_det.onnx
36a66a68097e88b103e0f60f489e88c7239d3ea79d96fbac2d80ac9d134944cd eslav_pp-ocrv5_mobile_rec.onnx
3e95f1581557162870cacdba5af91a4c6be2890710d395b0c3c7578e7ee5e6eb ppocrv5_eslav_dict.txt
EOF
