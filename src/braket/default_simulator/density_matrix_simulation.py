# Copyright 2019-2019 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You
# may not use this file except in compliance with the License. A copy of
# the License is located at
#
#     http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is
# distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF
# ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.

from typing import List, Tuple, Union

import numpy as np

from braket.default_simulator.operation import GateOperation, KrausOperation, Observable
from braket.default_simulator.simulation import Simulation


class DensityMatrixSimulation(Simulation):
    """
    This class tracks the evolution of the density matrix of a quantum system with
    `qubit_count` qubits. The state of system evolves by applications of `GateOperation`s
    and `KrausOperation`s using the `evolve()` method.
    """

    def __init__(self, qubit_count: int, shots: int):
        """
        Args:
            qubit_count (int): The number of qubits being simulated.
            shots (int): The number of samples to take from the simulation.
                If set to 0, only results that do not require sampling, such as density matrix
                or expectation, are generated.
        """
        super().__init__(qubit_count=qubit_count, shots=shots)
        initial_state = np.zeros((2 ** qubit_count, 2 ** qubit_count), dtype=complex)
        initial_state[0, 0] = 1
        self._density_matrix = initial_state
        self._post_observables = None

    def evolve(self, operations: List[Union[GateOperation, KrausOperation]]) -> None:
        """Evolves the state of the simulation under the action of the specified gate
        and noise operations.

        Args:
            operations (List[Union[GateOperation, KrausOperation]]): Operations to apply for
                evolving the state of the simulation.

        Note:
            This method mutates the state of the simulation.
        """
        self._density_matrix = DensityMatrixSimulation._apply_operations(
            self._density_matrix, self._qubit_count, operations
        )

    def apply_observables(self, observables: List[Observable]) -> None:
        """Applies the diagonalizing matrices of the given observables
        to the state of the simulation.

        This method can only be called once.

        Args:
            observables (List[Observable]): The observables to apply

        Raises:
            RuntimeError: If this method is called more than once
        """
        if self._post_observables is not None:
            raise RuntimeError("Observables have already been applied.")
        operations = list(
            sum(
                [observable.diagonalizing_gates(self._qubit_count) for observable in observables],
                (),
            )
        )
        self._post_observables = DensityMatrixSimulation._apply_operations(
            self._density_matrix, self._qubit_count, operations
        )

    @staticmethod
    def _apply_operations(
        state: np.ndarray, qubit_count: int, operations: List[Union[GateOperation, KrausOperation]]
    ) -> np.ndarray:
        """Applies the gate and noise operations to the density matrix.

        Args:
            state (np.ndarray): initial density matrix
            qubit_count (int): number of qubits in the circuit
            operations (List[Union[GateOperation, KrausOperation]]): list of GateOperation and
                KrausOperation to be applied to the density matrix

        Returns:
            state (np.ndarray): output density matrix
        """
        dm_tensor = np.reshape(state, [2] * 2 * qubit_count)
        for operation in operations:
            targets = operation.targets

            if isinstance(operation, (GateOperation, Observable)):
                matrix = operation.matrix
                if len(targets) > 3:
                    dm_tensor = DensityMatrixSimulation._apply_gate(
                        dm_tensor, qubit_count, matrix, targets
                    )
                else:
                    superop = np.kron(matrix, matrix.conjugate())
                    dm_tensor = DensityMatrixSimulation._apply_gate_superop(
                        dm_tensor, qubit_count, superop, targets
                    )

            if isinstance(operation, KrausOperation):
                dm_tensor = DensityMatrixSimulation._apply_kraus(
                    dm_tensor, qubit_count, operation.matrices, targets
                )

        return np.reshape(dm_tensor, (2 ** qubit_count, 2 ** qubit_count))

    def retrieve_samples(self) -> List[int]:
        """Retrieves samples of states from the density matrix of the simulation,
        based on the probabilities.

        Returns:
            List[int]: List of states sampled according to their probabilities
            in the density matrix. Each integer represents the decimal encoding of the
            corresponding computational basis state.
        """
        return np.random.choice(
            self._density_matrix.shape[0], p=self.probabilities, size=self._shots
        )

    @property
    def density_matrix(self) -> np.ndarray:
        """
        np.ndarray: The density matrix specifying the current state of the simulation.
        """
        return self._density_matrix

    @property
    def state_with_observables(self) -> np.ndarray:
        """
        np.ndarray: The final density matrix of the simulation after application of observables.

        Raises:
            RuntimeError: If observables have not been applied
        """
        if self._post_observables is None:
            raise RuntimeError("No observables applied")
        return self._post_observables

    @property
    def probabilities(self) -> np.ndarray:
        """
        np.ndarray: The probabilities of each computational basis state of the current density
            matrix of the simulation.
        """
        return self._probabilities(self.density_matrix)

    def _probabilities(self, state) -> np.ndarray:
        """The probabilities of each computational basis state of a given density matrix.

        Args:
            state (np.ndarray): a density matrix

        Returns:
            probabilities (np.ndarray)
        """
        prob = np.real(np.diag(state))
        prob_list = prob.copy()
        tol = 1e-20
        prob_list[abs(prob_list) < tol] = 0.0
        prob_list[prob_list < 0] = 0.0
        return prob_list

    def _apply_gate(
        state: np.ndarray, qubit_count: int, matrix: np.ndarray, targets: Tuple[int, ...]
    ) -> np.ndarray:
        """Apply a matrix M to a density matrix D according to:

            .. math::
                D \rightarrow M D M^{\\dagger}

        Args:
            state (np.ndarray): initial density matrix
            qubit_count (int): number of qubits in the circuit
            matrix (np.ndarray): matrix to be applied to the density matrix
            targets (Tuple[int,...]): qubits of the density matrix the matrix applied to.

        Returns:
            state (np.ndarray): output density matrix
        """
        # left product
        gate_matrix = np.reshape(matrix, [2] * len(targets) * 2)
        dm_targets = targets
        axes = (
            np.arange(len(targets), 2 * len(targets)),
            dm_targets,
        )
        state = np.tensordot(gate_matrix, state, axes=axes)

        # Arrange the index to the correct place.
        unused_idxs = [idx for idx in range(2 * qubit_count) if idx not in dm_targets]
        permutation = list(dm_targets) + unused_idxs
        inverse_permutation = np.argsort(permutation)
        state = np.transpose(state, inverse_permutation)

        # right product
        gate_matrix = np.reshape(matrix.conjugate(), [2] * len(targets) * 2)
        dm_targets = tuple(i + qubit_count for i in targets)
        axes = (
            np.arange(len(targets), 2 * len(targets)),
            dm_targets,
        )
        state = np.tensordot(gate_matrix, state, axes=axes)

        # Arrange the index to the correct place.
        unused_idxs = [idx for idx in range(2 * qubit_count) if idx not in dm_targets]
        permutation = list(dm_targets) + unused_idxs
        inverse_permutation = np.argsort(permutation)
        state = np.transpose(state, inverse_permutation)

        return state

    def _apply_gate_superop(
        state: np.ndarray, qubit_count: int, superop: np.ndarray, targets: Tuple[int, ...]
    ) -> np.ndarray:
        """Apply a superoperator to a density matrix

        Args:
            state (np.ndarray): initial density matrix
            qubit_count (int): number of qubits in the circuit
            superop (np.ndarray): superoperator to be applied to the density matrix
            targets (Tuple[int,...]): qubits of the density matrix the superoperator applied to.

        Returns:
            state (np.ndarray): output density matrix
        """
        targets_new = targets + tuple([target + qubit_count for target in targets])
        superop = np.reshape(superop, [2] * len(targets_new) * 2)
        axes = (
            np.arange(len(targets_new), 2 * len(targets_new)),
            targets_new,
        )
        state = np.tensordot(superop, state, axes=axes)

        # Arrange the index to the correct place.
        unused_idxs = [idx for idx in range(2 * qubit_count) if idx not in targets_new]
        permutation = list(targets_new) + unused_idxs
        inverse_permutation = np.argsort(permutation)
        state = np.transpose(state, inverse_permutation)

        return state

    def _apply_kraus(
        state: np.ndarray, qubit_count: int, matrices: List[np.ndarray], targets: Tuple[int, ...]
    ) -> np.ndarray:
        """Apply a list of matrices {E_i} to a density matrix D according to:

            .. math::
                D \rightarrow \\sum_i E_i D E_i^{\\dagger}

        Args:
            state (np.ndarray): initial density matrix
            qubit_count (int): number of qubits in the circuit
            matrices (List[np.ndarray]): matrices to be applied to the density matrix
            targets (Tuple[int,...]): qubits of the density matrix the matrices applied to.

        Returns:
            state (np.ndarray): output density matrix
        """
        if len(targets) > 4:
            new_state = sum(
                DensityMatrixSimulation._apply_gate(state, qubit_count, matrix, targets)
                for matrix in matrices
            )
        else:
            superop = sum(np.kron(matrix, matrix.conjugate()) for matrix in matrices)
            new_state = DensityMatrixSimulation._apply_gate_superop(
                state, qubit_count, superop, targets
            )
        return new_state
