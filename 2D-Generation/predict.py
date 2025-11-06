from denoising_diffusion_pytorch import Unet, GaussianDiffusion, Trainer
import os
import pickle
import torch

def predict_prepare():
    # Auto-detect: use GPU if available, otherwise CPU
    if torch.cuda.is_available():
        os.environ["CUDA_VISIBLE_DEVICES"] = "0"
    else:
        os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
        print("CUDA not available. Running on CPU...")
    
    results_folder = "./predict_model"
    train_num_workers = 0
    with open(os.path.join(results_folder, "params.pkl"), "rb") as f:
        params = pickle.load(f)

    model = Unet(**params["unet_dict"])

    # params["diffusion_dict"]["sampling_timesteps"] = 50
    diffusion = GaussianDiffusion(model, **params["diffusion_dict"])

    trainer = Trainer(
        diffusion,
        "",
        "",
        "",
        **params["trainer_dict"],
        results_folder=results_folder,
        train_num_workers=train_num_workers,
        mode="predict",
        inject_step=40
    )

    trainer.predict_load(98)
    return trainer