import logging
from pathlib import Path
from typing import List, Optional, Tuple

import dask.dataframe as dd
import pandas as pd
import torch
from pytorch_lightning import LightningDataModule
from pytorch_lightning.utilities.types import EVAL_DATALOADERS, TRAIN_DATALOADERS
from torch.utils.data import DataLoader, random_split
from tqdm import tqdm

from rxitect.datasets.smiles_dataset import SmilesDataset
from rxitect.utils.smiles import SmilesTokenizer, randomize_smiles

tqdm.pandas()
logger = logging.getLogger(__name__)


class SmilesDataModule(LightningDataModule):
    def __init__(
        self,
        data_filepath: Path,
        train_val_test_split: Tuple[int, int, int],
        augment: bool = False,
        batch_size: int = 128,
        num_workers: int = 0,
        npartitions: Optional[int] = None,
        pin_memory: bool = False,
        random_state: int = 42,
    ) -> None:
        super().__init__()

        self.save_hyperparameters(logger=False)

        self.tokenizer = SmilesTokenizer()

    def prepare_data(self) -> None:
        pass
        # TODO: Download the tokenized ChEMBL file here
        # saves us the params if we init the vocab internally as well.

    def setup(self, stage: Optional[str] = None) -> None:
        num_samples = sum(self.hparams.train_val_test_split)
        data = pd.read_table(self.hparams.data_filepath, usecols=["smiles"]).sample(
            n=num_samples, random_state=self.hparams.random_state
        )

        if self.hparams.augment:
            # Atom order randomize SMILES
            if self.hparams.npartitions:
                logger.info(f"Randomizing SMILES using {self.hparams.npartitions} partitions...")
                ddf = dd.from_pandas(data, npartitions=self.hparams.npartitions)
                data["smiles"] = ddf.map_partitions(_dist_randomize_smiles, meta="str").compute(
                    scheduler="processes"
                )
            else:
                logger.info("Randomizing SMILES...")
                data["smiles"] = data["smiles"].progress_apply(randomize_smiles)

        # Initialize tokenizer
        self.tokenizer.fit(data["smiles"].values)

        data = SmilesDataset(data, self.tokenizer)
        # Create splits for train/val
        self.train_data, self.val_data, self.test_data = random_split(
            dataset=data,
            lengths=self.hparams.train_val_test_split,
            generator=torch.Generator().manual_seed(self.hparams.random_state),
        )

    def custom_collate_and_pad(self, batch: List[torch.Tensor]) -> List[torch.Tensor]:
        """
        Args:
            batch (List[str]): A list of vectorized smiles.

        Returns:
            A list containing the padded versions of the tensors that were passed in.
        """
        tensors = [torch.tensor(vectorized_smiles) for vectorized_smiles in batch]
        # Pad & Transpose, pytorch RNNs/Transformers expect (sequence,batch, features) dims
        tensors = torch.nn.utils.rnn.pad_sequence(tensors)
        return tensors

    def train_dataloader(self) -> TRAIN_DATALOADERS:
        return DataLoader(
            dataset=self.train_data,
            batch_size=self.hparams.batch_size,
            pin_memory=self.hparams.pin_memory,
            num_workers=self.hparams.num_workers,
            collate_fn=self.custom_collate_and_pad,
            shuffle=True,
        )

    def val_dataloader(self) -> EVAL_DATALOADERS:
        return DataLoader(
            dataset=self.val_data,
            batch_size=self.hparams.batch_size,
            pin_memory=self.hparams.pin_memory,
            num_workers=self.hparams.num_workers,
            collate_fn=self.custom_collate_and_pad,
            shuffle=False,
        )

    def test_dataloader(self) -> EVAL_DATALOADERS:
        return DataLoader(
            dataset=self.test_data,
            batch_size=self.hparams.batch_size,
            pin_memory=self.hparams.pin_memory,
            num_workers=self.hparams.num_workers,
            collate_fn=self.custom_collate_and_pad,
            shuffle=False,
        )


def _dist_randomize_smiles(df: pd.DataFrame) -> pd.DataFrame:
    return df["smiles"].apply(randomize_smiles)