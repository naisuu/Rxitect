import torch
from pyprojroot import here

root_path = here(project_files=[".here"])
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
chembl_26_size = 1_940_732
