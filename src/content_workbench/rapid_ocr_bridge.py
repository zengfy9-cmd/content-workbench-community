"""Executed by the isolated OCR interpreter, never imports workbench settings."""
import argparse
import json
from pathlib import Path


def yellow_mask(image):
    from PIL import ImageChops
    red,green,blue=image.convert('RGB').split()
    mask=ImageChops.multiply(red.point(lambda v:255 if v>140 else 0),green.point(lambda v:255 if v>110 else 0))
    # Yellow has both red AND green above blue; red slide headings must not pass.
    return ImageChops.multiply(mask,ImageChops.subtract(green,blue).point(lambda v:255 if v>65 else 0))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('manifest');parser.add_argument('output')
    parser.add_argument('--palette',default='normal',choices=('normal','yellow'))
    args=parser.parse_args()
    import rapidocr
    from rapidocr import RapidOCR
    from PIL import Image,ImageOps
    import numpy as np
    models=Path(rapidocr.__file__).parent/'models'
    paths={'Det.model_path':models/'PP-OCRv6_det_small.onnx','Rec.model_path':models/'PP-OCRv6_rec_small.onnx','Cls.model_path':models/'ch_ppocr_mobile_v2.0_cls_mobile.onnx'}
    if not all(path.is_file() for path in paths.values()):raise RuntimeError('Local OCR models missing; no automatic download')
    engine=RapidOCR(params={'Global.log_level':'error','EngineConfig.onnxruntime.intra_op_num_threads':2,'EngineConfig.onnxruntime.inter_op_num_threads':1,**{key:str(path) for key,path in paths.items()}})
    entries=json.loads(Path(args.manifest).read_text(encoding='utf-8'))
    with Path(args.output).open('w',encoding='utf-8') as out:
        for entry in entries:
            lines=[]
            with Image.open(entry['path']) as source:
                image=source.convert('RGB')
            if args.palette=='yellow':
                mask=yellow_mask(image);box=mask.getbbox()
                if box and box[3]-box[1]>=image.height*.25 and box[2]-box[0]>=image.width*.06:
                    image=ImageOps.invert(mask).crop(box).convert('RGB')
                    result=engine(np.asarray(image),use_det=False,use_cls=False)
                    for text,score in zip(result.txts or (),result.scores or ()):
                        if score>=.5:
                            lines.append({'text':text,'score':float(score),'words':[{'text':text,'x':box[0],'y':box[1],'width':box[2]-box[0],'height':box[3]-box[1]}]})
            else:
                result=engine(np.asarray(image),use_det=True,use_cls=False)
                if result.boxes is not None:
                    for box,text,score in zip(result.boxes,result.txts,result.scores):
                        xs=box[:,0];ys=box[:,1]
                        lines.append({'text':text,'score':float(score),'words':[{'text':text,'x':float(min(xs)),'y':float(min(ys)),'width':float(max(xs)-min(xs)),'height':float(max(ys)-min(ys))}]})
            out.write(json.dumps({'time':entry['time'],'path':entry['path'],'lines':lines},ensure_ascii=False)+'\n');out.flush()


if __name__=='__main__':main()
