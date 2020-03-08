import torch
import torch.nn as nn
import torch.nn.functional as F

import math
import numpy as np
from torch.nn.utils.rnn import PackedSequence
from ..layers.core import DNN


class SequencePoolingLayer(nn.Module):
    """The SequencePoolingLayer is used to apply pooling operation(sum,mean,max) on variable-length sequence feature/multi-value feature.

      Input shape
        - A list of two  tensor [seq_value,seq_len]

        - seq_value is a 3D tensor with shape: ``(batch_size, T, embedding_size)``

        - seq_len is a 2D tensor with shape : ``(batch_size, 1)``,indicate valid length of each sequence.

      Output shape
        - 3D tensor with shape: ``(batch_size, 1, embedding_size)``.

      Arguments
        - **mode**:str.Pooling operation to be used,can be sum,mean or max.

    """

    def __init__(self, mode='mean', supports_masking=False, device='cpu'):

        super(SequencePoolingLayer, self).__init__()
        if mode not in ['sum', 'mean', 'max']:
            raise ValueError('parameter mode should in [sum, mean, max]')
        self.supports_masking = supports_masking
        self.mode = mode
        self.eps = torch.FloatTensor([1e-8]).to(device)
        self.to(device)

    def _sequence_mask(self, lengths, maxlen=None, dtype=torch.bool):
        # Returns a mask tensor representing the first N positions of each cell.
        if maxlen is None:
            maxlen = lengths.max()
        row_vector = torch.arange(0, maxlen, 1)
        matrix = torch.unsqueeze(lengths, dim=-1)
        mask = row_vector < matrix

        mask.type(dtype)
        return mask

    def forward(self, seq_value_len_list):
        if self.supports_masking:
            uiseq_embed_list, mask = seq_value_len_list  # [B, T, E], [B, 1]
            mask = mask.float()
            user_behavior_length = torch.sum(mask, dim=-1, keepdim=True)
            mask = mask.unsqueeze(2)
        else:
            uiseq_embed_list, user_behavior_length = seq_value_len_list  # [B, T, E], [B, 1]
            mask = self._sequence_mask(user_behavior_length, maxlen=uiseq_embed_list.shape[1],
                                       dtype=torch.float32)  # [B, 1, maxlen]
            mask = torch.transpose(mask, 1, 2)  # [B, maxlen, 1]

        embedding_size = uiseq_embed_list.shape[-1]

        mask = torch.repeat_interleave(mask, embedding_size, dim=2)  # [B, maxlen, E]

        if self.mode == 'max':
            hist = uiseq_embed_list - (1 - mask) * 1e9
            hist = torch.max(hist, dim=1, keepdim=True)[0]
            return hist
        hist = torch.sum(uiseq_embed_list * mask, dim=1, keepdim=False)

        if self.mode == 'mean':
            hist = torch.div(hist, user_behavior_length.type(torch.float32) + self.eps)

        hist = torch.unsqueeze(hist, dim=1)
        return hist


# class AttentionSequencePoolingLayer(nn.Module):
#     """The Attentional sequence pooling operation used in DIN.
#
#       Input shape
#         - A list of three tensor: [query,keys,keys_length]
#
#         - query is a 3D tensor with shape:  ``(batch_size, 1, embedding_size)``
#
#         - keys is a 3D tensor with shape:   ``(batch_size, T, embedding_size)``
#
#         - keys_length is a 2D tensor with shape: ``(batch_size, 1)``
#
#       Output shape
#         - 3D tensor with shape: ``(batch_size, 1, embedding_size)``
#
#       Arguments
#         - **att_hidden_units**: List of positive integer, the attention net layer number and units in each layer.
#
#         - **embedding_dim**: Dimension of the input embeddings.
#
#         - **activation**: Activation function to use in attention net.
#
#         - **weight_normalization**: bool.Whether normalize the attention score of local activation unit.
#
#       References
#         - [Zhou G, Zhu X, Song C, et al. Deep interest network for click-through rate prediction[C]//Proceedings of the 24th ACM SIGKDD International Conference on Knowledge Discovery & Data Mining. ACM, 2018: 1059-1068.](https://arxiv.org/pdf/1706.06978.pdf)
#     """
#
#     def __init__(self, att_hidden_units=[80, 40], embedding_dim=4, activation='Dice', weight_normalization=False):
#         super(AttentionSequencePoolingLayer, self).__init__()
#
#         self.local_att = LocalActivationUnit(hidden_units=att_hidden_units, embedding_dim=embedding_dim,
#                                              activation=activation)
#
#     def forward(self, query, keys, keys_length):
#         # query: [B, 1, E], keys: [B, T, E], keys_length: [B, 1]
#         # TODO: Mini-batch aware regularization in originial paper [Zhou G, et al. 2018] is not implemented here. As the authors mentioned
#         #       it is not a must for small dataset as the open-sourced ones.
#         attention_score = self.local_att(query, keys)
#         attention_score = torch.transpose(attention_score, 1, 2)  # B * 1 * T
#
#         # define mask by length
#         keys_length = keys_length.type(torch.LongTensor)
#         mask = torch.arange(keys.size(1))[None, :] < keys_length[:, None]  # [1, T] < [B, 1, 1]  -> [B, 1, T]
#
#         # mask
#         output = torch.mul(attention_score, mask.type(torch.FloatTensor))  # [B, 1, T]
#
#         # multiply weight
#         output = torch.matmul(output, keys)  # [B, 1, E]
#
#         return output


class KMaxPooling(nn.Module):
    """K Max pooling that selects the k biggest value along the specific axis.

      Input shape
        -  nD tensor with shape: ``(batch_size, ..., input_dim)``.

      Output shape
        - nD tensor with shape: ``(batch_size, ..., output_dim)``.

      Arguments
        - **k**: positive integer, number of top elements to look for along the ``axis`` dimension.

        - **axis**: positive integer, the dimension to look for elements.

     """

    def __init__(self, k, axis, device='cpu'):
        super(KMaxPooling, self).__init__()
        self.k = k
        self.axis = axis
        self.to(device)

    def forward(self, input):
        if self.axis < 0 or self.axis >= len(input.shape):
            raise ValueError("axis must be 0~%d,now is %d" %
                             (len(input.shape) - 1, self.axis))

        if self.k < 1 or self.k > input.shape[self.axis]:
            raise ValueError("k must be in 1 ~ %d,now k is %d" %
                             (input.shape[self.axis], self.k))

        out = torch.topk(input, k=self.k, dim=self.axis, sorted=True)[0]
        return out


class AGRUCell(nn.Module):
    """ Attention based GRU (AGRU)

        Reference:
        -  Deep Interest Evolution Network for Click-Through Rate Prediction[J]. arXiv preprint arXiv:1809.03672, 2018.
    """

    def __init__(self, input_size, hidden_size, bias=True):
        super(AGRUCell, self).__init__()
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.bias = bias
        # (W_ir|W_iz|W_ih)
        self.weight_ih = nn.Parameter(torch.Tensor(3 * hidden_size, input_size))
        self.register_parameter('weight_ih', self.weight_ih)
        # (W_hr|W_hz|W_hh)
        self.weight_hh = nn.Parameter(torch.Tensor(3 * hidden_size, hidden_size))
        self.register_parameter('weight_hh', self.weight_hh)
        if bias:
            # (b_ir|b_iz|b_ih)
            self.bias_ih = nn.Parameter(torch.Tensor(3 * hidden_size))
            self.register_parameter('bias_ih', self.bias_ih)
            # (b_hr|b_hz|b_hh)
            self.bias_hh = nn.Parameter(torch.Tensor(3 * hidden_size))
            self.register_parameter('bias_hh', self.bias_hh)
        else:
            self.register_parameter('bias_ih', None)
            self.register_parameter('bias_hh', None)

    def forward(self, input, hx, att_score):
        gi = F.linear(input, self.weight_ih, self.bias_ih)
        gh = F.linear(hx, self.weight_hh, self.bias_hh)
        i_r, i_z, i_n = gi.chunk(3, 1)
        h_r, h_z, h_n = gh.chunk(3, 1)

        reset_gate = torch.sigmoid(i_r + h_r)
        # update_gate = torch.sigmoid(i_z + h_z)
        new_state = torch.tanh(i_n + reset_gate * h_n)

        att_score = att_score.view(-1, 1)
        hy = (1. - att_score) * hx + att_score * new_state
        return hy


class AUGRUCell(nn.Module):
    """ Effect of GRU with attentional update gate (AUGRU)

        Reference:
        -  Deep Interest Evolution Network for Click-Through Rate Prediction[J]. arXiv preprint arXiv:1809.03672, 2018.
    """

    def __init__(self, input_size, hidden_size, bias=True):
        super(AUGRUCell, self).__init__()
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.bias = bias
        # (W_ir|W_iz|W_ih)
        self.weight_ih = nn.Parameter(torch.Tensor(3 * hidden_size, input_size))
        self.register_parameter('weight_ih', self.weight_ih)
        # (W_hr|W_hz|W_hh)
        self.weight_hh = nn.Parameter(torch.Tensor(3 * hidden_size, hidden_size))
        self.register_parameter('weight_hh', self.weight_hh)
        if bias:
            # (b_ir|b_iz|b_ih)
            self.bias_ih = nn.Parameter(torch.Tensor(3 * hidden_size))
            self.register_parameter('bias_ih', self.bias_ih)
            # (b_hr|b_hz|b_hh)
            self.bias_hh = nn.Parameter(torch.Tensor(3 * hidden_size))
            self.register_parameter('bias_ih', self.bias_hh)
        else:
            self.register_parameter('bias_ih', None)
            self.register_parameter('bias_hh', None)

    def forward(self, input, hx, att_score):
        gi = F.linear(input, self.weight_ih, self.bias_ih)
        gh = F.linear(hx, self.weight_hh, self.bias_hh)
        i_r, i_z, i_n = gi.chunk(3, 1)
        h_r, h_z, h_n = gh.chunk(3, 1)

        reset_gate = torch.sigmoid(i_r + h_r)
        update_gate = torch.sigmoid(i_z + h_z)
        new_state = torch.tanh(i_n + reset_gate * h_n)

        att_score = att_score.view(-1, 1)
        update_gate = att_score * update_gate
        hy = (1. - update_gate) * hx + update_gate * new_state
        return hy


class DynamicGRU(nn.Module):
    def __init__(self, input_size, hidden_size, bias=True, gru_type='AGRU'):
        super(DynamicGRU, self).__init__()
        self.input_size = input_size
        self.hidden_size = hidden_size

        if gru_type == 'AGRU':
            self.rnn = AGRUCell(input_size, hidden_size, bias)
        elif gru_type == 'AUGRU':
            self.rnn = AUGRUCell(input_size, hidden_size, bias)

    def forward(self, input, att_scores=None, hx=None):
        if not isinstance(input, PackedSequence) or not isinstance(att_scores, PackedSequence):
            raise NotImplementedError("DynamicGRU only supports packed input and att_scores")

        input, batch_sizes, sorted_indices, unsorted_indices = input
        att_scores, _, _, _ = att_scores

        max_batch_size = int(batch_sizes[0])
        if hx is None:
            hx = torch.zeros(max_batch_size, self.hidden_size,
                             dtype=input.dtype, device=input.device)

        outputs = torch.zeros(input.size(0), self.hidden_size,
                              dtype=input.dtype, device=input.device)

        begin = 0
        for batch in batch_sizes:
            new_hx = self.rnn(
                input[begin:begin + batch],
                hx[0:batch],
                att_scores[begin:begin + batch])
            outputs[begin:begin + batch] = new_hx
            hx = new_hx
            begin += batch
        return PackedSequence(outputs, batch_sizes, sorted_indices, unsorted_indices)


class AttentionNet(nn.Module):
    def __init__(self, input_size,
                 dnn_hidden_units,
                 activation='relu',
                 l2_reg=0,
                 dropout_rate=0,
                 init_std=0.0001,
                 use_bn=False,
                 device='cpu',
                 return_scores=False):
        super(AttentionNet, self).__init__()
        self.return_scores = return_scores
        self.mlp = DNN(input_size * 4,
                       dnn_hidden_units,
                       activation=activation,
                       l2_reg=l2_reg,
                       dropout_rate=dropout_rate,
                       use_bn=use_bn,
                       init_std=init_std,
                       device=device)
        self.fc = nn.Linear(dnn_hidden_units[-1], 1)
        for name, tensor in self.fc.named_parameters():
            if 'weight' in name:
                nn.init.normal_(tensor, mean=0, std=init_std)

    def forward(self, query, keys, keys_length):
        """
        Parameters
        ----------
        query: 2D tensor, [B, H]
        keys: 3D tensor, [B, T, H]
        keys_length: 1D tensor, [B]

        Returns
        -------
        outputs: 2D tensor, if return_scores=False [B, H], otherwise [B, T]
        """
        batch_size, max_length, dim = keys.size()

        query = query.unsqueeze(1).expand(-1, max_length, -1)

        din_all = torch.cat(
            [query, keys, query - keys, query * keys], dim=-1)

        din_all = din_all.view(batch_size * max_length, -1)

        outputs = self.mlp(din_all)

        outputs = self.fc(outputs).view(batch_size, max_length)  # [B, T]

        # Scale
        outputs = outputs / (dim ** 0.5)

        # Mask
        mask = (torch.arange(max_length, device=keys_length.device).repeat(
            batch_size, 1) < keys_length.view(-1, 1))
        outputs[~mask] = -np.inf

        # Activation
        outputs = F.softmax(outputs, dim=1)  # [B, T]

        if not self.return_scores:
            # Weighted sum
            outputs = torch.matmul(
                outputs.unsqueeze(1), keys).squeeze(1)  # [B, H]

        return outputs
