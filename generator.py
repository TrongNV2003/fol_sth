import argparse
from datetime import datetime

import torch
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
from transformers import LongformerTokenizer, LongformerForMultipleChoice
from prepare import prepare_qa_input, prepare_distractor_input

def inference(
    source_path, # path to source (text) file
    summary_path, # path to summary (text) file
    mqag_variant, # mqag_src | mqag_sum
    num_samples, # number of questions to be drawn
    generation_model1_path, # path to Question+Answer Gen (t5-large)
    generation_model_type1,
    generation_model2_path, # path to Distractor Gen (t5-large)
    generation_model_type2,
    use_gpu, # whether or not to use GPU (if available)
    verbose, # whether or not to print information
):

    # ----- using GPU or CPU ----- #
    if use_gpu and torch.cuda.is_available():
        torch_device = 'cuda'
    else:
        torch_device = 'cpu'

    # -------- load data --------- #
    # 1) we expect it to be in the format --- one line per document
    # 2) len(source) == len(summary)
    with open(source_path) as f:
        source_lines = f.readlines()
    with open(summary_path) as f:
        summary_lines = f.readlines()
    assert len(source_lines) == len(summary_lines), "len(source) must match len(summary)"
    len_data = len(source_lines)
    print("len_data:", len_data)

    # ---------- Model ----------- #
    max_length = 512
    generation_tokenizer = AutoTokenizer.from_pretrained('VietAI/vit5-base', model_max_length=max_length)
    generation_tokenizer.add_special_tokens({"sep_token": "<sep>"})
    # model1: question generation
    qg_model = AutoModelForSeq2SeqLM.from_pretrained(generation_model_type1)
    if torch_device == "cuda":
        qg_model.cuda()
        state = torch.load(generation_model1_path)
    else:
        state = torch.load(generation_model1_path, map_location=torch.device('cpu'))
    model_state_dict = state['model']
    qg_model.load_state_dict(model_state_dict)
    qg_model.eval()
    print('Question+AnswerGeneration Model loaded:', generation_model1_path)

    # model2: distractor generation
    distractor_model = AutoModelForSeq2SeqLM.from_pretrained(generation_model_type2)
    if torch_device == "cuda":
        distractor_model.cuda()
        state = torch.load(generation_model2_path)
    else:
        state = torch.load(generation_model2_path, map_location=torch.device('cpu'))
    model_state_dict = state['model']
    distractor_model.load_state_dict(model_state_dict)
    distractor_model.eval()
    print('DistractorGeneration Model loaded:', generation_model2_path)
    
    max_repeated_sampling = 2 # how many times to repeat the sampling process until there is a valid question (it should be 1)

    for idx in range(len_data):
        doc_x = source_lines[idx]
        sum_y = summary_lines[idx]
        # Stage1: Question Answer Generation
        if mqag_variant == 'mqag_src':
            context_for_generation = doc_x
        elif mqag_variant == 'mqag_sum':
            context_for_generation = sum_y

        qa_input_ids = prepare_qa_input(
                generation_tokenizer,
                context=context_for_generation, # doc_x, sum_y
                torch_device=torch_device
            )
        for q_ in range(num_samples):
            for _ in range(max_repeated_sampling):
                outputs = qg_model.generate(
                    qa_input_ids,
                    max_new_tokens=128,
                    do_sample=True,
                )
                question_answer = generation_tokenizer.decode(outputs[0], skip_special_tokens=False)
                question_answer = question_answer.replace(generation_tokenizer.pad_token, "").replace(generation_tokenizer.eos_token, "")
                question_answer_split = question_answer.split(generation_tokenizer.sep_token)
                if len(question_answer_split) == 2:
                    # valid Question + Annswer output
                    valid_question_answer = True
                    break
                else:
                    valid_question_answer = False

            if valid_question_answer == False:
                raise Exception("max_repeated_sampling exceeded")

            question = question_answer_split[0].strip()
            answer = question_answer_split[1].strip()

            # Stage2: Distractor Generation
            distractor_input_ids = prepare_distractor_input(
                    generation_tokenizer,
                    context = context_for_generation, # doc_x, sum_y
                    question = question,
                    answer = answer,
                    separator = generation_tokenizer.sep_token,
                    torch_device=torch_device
            )

            outputs = distractor_model.generate(
                distractor_input_ids,
                max_new_tokens=128,
                do_sample=True,
            )

            distractors = generation_tokenizer.decode(outputs[0], skip_special_tokens=False)
            distractors = distractors.replace(generation_tokenizer.pad_token, "").replace(generation_tokenizer.eos_token, "")
            distractors = [y.strip() for y in distractors.split(generation_tokenizer.sep_token)]
            options = [answer] + distractors


            print("[{}] document={}/{}, multiple-choice question={}/{}".format(str(datetime.now()), idx+1, len_data, q_+1, num_samples))
            if verbose:
                print("Question:", question)
                print('\n'.join([f"({o_+1}) {option}" for o_, option in enumerate(options)]))

            print("---------------------------------------------------------------------------------------")

def add_arguments(parser):
    '''Build Argument Parser'''
    parser.register("type", "bool", lambda v: v.lower() == "true")
    parser.add_argument('--source_path', type=str, required=True)
    parser.add_argument('--summary_path', type=str, required=True)
    parser.add_argument('--mqag_variant', type=str, required=True)
    parser.add_argument('--num_samples', type=int, default=20)
    parser.add_argument('--generation_model1_path', type=str, required=True)
    parser.add_argument('--generation_model_type1', type=str, default='t5-base-question-generator')
    parser.add_argument('--generation_model2_path', type=str, required=True)
    parser.add_argument('--generation_model_type2', type=str, default='t5-base-distractor-generator')
    parser.add_argument('--use_gpu', type="bool", nargs="?", const=True, default=True)
    parser.add_argument('--verbose', type="bool", nargs="?", const=True, default=False)
    return parser

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser = add_arguments(parser)
    kwargs = vars(parser.parse_args())

    # simple argument checking
    assert kwargs['mqag_variant'] in ['mqag_sum', 'mqag_src'], "mqag_varaint not exist, use only mqag_sum, mqag_sum"
    assert kwargs['num_samples'] > 0, "num_samples > 0 error"

    with torch.no_grad():
        inference(**kwargs)