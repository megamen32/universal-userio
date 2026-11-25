use oar_ocr::{core::config::{OrtExecutionProvider, OrtSessionConfig}, oarocr::OAROCRBuilder};
use std::{io::{self, Read}, path::Path};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let models = std::env::args().nth(1).ok_or("missing model directory")?;
    let model = |name: &str| Path::new(&models).join(name).to_string_lossy().into_owned();
    let mut bytes = Vec::new();
    io::stdin().lock().take(4 * 1024 * 1024 + 1).read_to_end(&mut bytes)?;
    if bytes.is_empty() || bytes.len() > 4 * 1024 * 1024 { return Err("invalid input size".into()); }
    let mut reader = image::ImageReader::new(io::Cursor::new(bytes)).with_guessed_format()?;
    let mut limits = image::Limits::default();
    limits.max_image_width = Some(4096);
    limits.max_image_height = Some(4096);
    limits.max_alloc = Some(128 * 1024 * 1024);
    reader.limits(limits);
    let image = reader.decode()?.to_rgb8();
    let session = OrtSessionConfig::new().with_intra_threads(1).with_inter_threads(1)
        .with_parallel_execution(false).with_execution_providers(vec![OrtExecutionProvider::CPU]);
    let ocr = OAROCRBuilder::new(model("pp-ocrv6_tiny_det.onnx"), model("eslav_pp-ocrv5_mobile_rec.onnx"), model("ppocrv5_eslav_dict.txt"))
        .ort_session(session).image_batch_size(1).region_batch_size(1).build()?;
    let pool = rayon::ThreadPoolBuilder::new().num_threads(1).build()?;
    let mut remaining = 64_000usize;
    for page in pool.install(|| ocr.predict(vec![image]))? {
        for region in page.text_regions {
            if let Some(text) = region.text {
                let line: String = text.trim().chars().take(remaining).collect();
                remaining = remaining.saturating_sub(line.chars().count());
                if !line.is_empty() { println!("{line}"); }
                if remaining == 0 { return Ok(()); }
            }
        }
    }
    Ok(())
}
