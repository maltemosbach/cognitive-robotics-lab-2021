from lab import Agent
from lab.models.planners.cross_entropy_method import CEMPlanner
import numpy as np
import torch
from torch import Tensor
from torch.distributions import Normal
from torch.distributions import Distribution
from torch.distributions.kl import kl_divergence
import torch.nn as nn
from torch.nn import functional as F
from typing import Dict, List, Tuple
from lab.replay_buffer.ndarray_tuple import ndarray_tuple


class PlaNet(Agent):
    def __init__(self, action_noise: float, batch_size: int, candidates: int, chunk_size: int,
                 criterion: str, det_state_size: int, free_nats: float, gamma: float, global_kl_beta: float,
                 gradient_clip_val: float, latent_dynamics_model: str, network_configs: Dict, optimization_iters: int,
                 optimizer: Dict, overshooting_distance: int, overshooting_kl_beta: float,
                 overshooting_reward_scale: float, planning_horizon: int, stoch_state_size: int,
                 top_candidates: int) -> None:
        super(PlaNet, self).__init__(locals(), model_based=True)
        self.global_prior = Normal(torch.zeros(batch_size, stoch_state_size), torch.ones(batch_size, stoch_state_size))

        self.make_transition = ndarray_tuple("Transition", ["observation", "goal", "action", "reward", "done"])

    def initialize(self, env_spaces: Dict) -> None:
        assert env_spaces["has_continuous_actions"], "Cannot use PlaNet on environment with discrete actions."
        observation_size = env_spaces["observation_size"]
        self.env_spaces = env_spaces

        if self.latent_dynamics_model == "rssm":
            self.dynamics_model = # TODO: Your latent dynamics model here
        elif self.latent_dynamics_model == "ssm":
            self.dynamics_model = # TODO: Your latent dynamics model here
        elif self.latent_dynamics_model == "rnn":
            self.dynamics_model = # TODO: Your latent dynamics model here
        else:
            assert False

        self.reward_model =  # TODO: Your reward model here

        self.planner = CEMPlanner(env_spaces["action_size"], self.planning_horizon, self.optimization_iters,
                                  self.candidates, self.top_candidates, self.dynamics_model, self.reward_model)

    def select_action(self, observation: np.ndarray, goal: np.ndarray, explore: bool, episode_start: bool) \
            -> np.ndarray:
        if episode_start:
            self.state, self.action = self._init_state_and_action()

        observation = torch.from_numpy(observation).to(self.device).unsqueeze(0)
        next_state = self.dynamics_model(self.state, self.action, observation)  # s_t ~ p(s_t | s_t-1, e_t)
        self.state = next_state

        # Get next action from planner.
        action = self.planner(self.state).squeeze(dim=0)

        if explore:
            action += self.action_noise * torch.randn(self.env_spaces["action_size"]).to(self.device)

        self.action = action.unsqueeze(0)
        return np.clip(action.cpu().numpy(), -1., 1.)

    def configure_optimizers(self):
        self._configure_optimizers([self.optimizer["type"]], [self.optimizer["learning_rate"]], [self.parameters()],
                                   [self.optimizer["lr_scheduler"]])

    def configure_sampling_args(self):
        return {"batch_size": self.batch_size, "sequence_len": self.chunk_size}

    def learn_on_batch(self, batch) -> Dict:
        state_sequence = self._predict_state_sequence(batch)
        loss =  # TODO: Your loss here

        self.optimizer.zero_grad()
        loss.backward()
        total_grad_norm = nn.utils.clip_grad_norm_(self.parameters(), self.gradient_clip_val, norm_type=2)
        self.optimizer.step()

        return losses  # Return a dict of your losses to have them logger to Tensorboard

    def _predict_state_sequence(self, batch: Tuple, prev_state: Dict = None) -> Dict:
        observations, goals, actions, rewards, dones = batch
        if prev_state is None:
            prev_state = self._init_state()

        T = actions.size(0)
        prior_states, posterior_states = [], []
        for t in range(T - 1):
            if self.latent_dynamics_model in ["ssm", "rssm"]:
                prev_state["state"]["stoch_state"] = prev_state["state"]["stoch_state"] * (1 - dones[t].type(torch.uint8).
                                                                         unsqueeze(1).expand_as(prev_state["state"]["stoch_state"]))
            prev_action = actions[t]
            observation = observations[t + 1] if observations is not None else None
            state = self.dynamics_model(prev_state, prev_action, observation)
            prev_state = state

            prior_states.append(state["out"][0])
            posterior_states.append(state["out"][1])
        prior_states_seq = self._stack_dicts(prior_states, dim=0)
        posterior_states_seq = self._stack_dicts(posterior_states, dim=0)
        return {"prior": prior_states_seq,
                "posterior": posterior_states_seq}

    @staticmethod
    def _stack_dicts(list_of_dicts: List, dim: int = 0) -> Dict:
        stacked_dict = {}
        for key in list_of_dicts[0].keys():
            stacked_dict[key] = []
        for curr_dict in list_of_dicts:
            for key in stacked_dict.keys():
                stacked_dict[key].append(curr_dict[key])
        for key in stacked_dict.keys():
            stacked_dict[key] = torch.stack(stacked_dict[key], dim=dim)
        return stacked_dict

    def _init_state(self) -> Dict:
        init_det_state = torch.zeros(self.batch_size, self.det_state_size).to(self.device)
        init_stoch_state = torch.zeros(self.batch_size, self.stoch_state_size).to(self.device)
        init_state = {"state": {"det_state": init_det_state, "stoch_state": init_stoch_state}}
        return init_state

    def _init_state_and_action(self) -> Tuple[Dict, Tensor]:
        init_det_state = torch.zeros(1, self.det_state_size).to(self.device)
        init_stoch_state = torch.zeros(1, self.stoch_state_size).to(self.device)
        init_state = {"state": {"det_state": init_det_state, "stoch_state": init_stoch_state}}
        init_action = torch.zeros(1, self.env_spaces["action_size"]).to(self.device)
        return init_state, init_action

    def _log_reconstructions(self, observations, predicted_observations):
        self.log("observation", torch.clip(observations[:, 0] + 0.5, 0., 1.))
        self.log("reconstruction", torch.clip(predicted_observations[:, 0] + 0.5, 0., 1.))

    def store_transition(self, observation, goal, action, reward, done, next_observation, next_goal) -> None:
        transition = self.make_transition(observation=observation, goal=goal, action=action, reward=reward,
                                          done=done)
        self.replay_buffer.store_transition(transition)
