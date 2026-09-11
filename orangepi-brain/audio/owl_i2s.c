// SPDX-License-Identifier: GPL-2.0
/*
 * owl_i2s.c - Robot Owl I2S sound card for the Orange Pi Zero 3W (A733).
 *
 * Wires the A733's I2S0 controller to the MAX98357A amplifier (TX) and the
 * ICS43434 MEMS microphone (RX). Neither device is an I2C-programmable ASoC
 * codec, so the card pairs the CPU DAI with the kernel's dummy codec; all the
 * real work is programming the A733 I2S controller correctly.
 *
 * The A733 BSP I2S controller is not a standard ASoC CPU DAI. Two things the
 * stock machine driver does that an external driver must reproduce, modelled
 * on the proven Whisplay A733 driver:
 *
 *   1. It does not derive its PLL implicitly. set_pll() must be called before
 *      set_sysclk(); the reverse leaves pllclk_freq at zero and the following
 *      CPU hw_params fails in a tight userspace retry loop that can crash the
 *      vendor kernel.
 *
 *   2. It carries I2S's one-bit data delay outside the standard set_fmt() API.
 *      set_fmt(I2S) leaves every TX/RX channel offset at zero, which makes
 *      capture one bit early (the sign bit is dropped: samples alternate
 *      between zero and positive full scale). The fix writes the same
 *      TX_OFFSET/RX_OFFSET fields the vendor's private notifier uses, and it
 *      must be re-applied after EVERY set_fmt() because the vendor callback
 *      rewrites those fields there.
 *
 * The clock is fixed at 48 kHz (the only rate the A733 BSP path is validated
 * for): BCLK = 64 x LRCLK, two 32-bit slots. The mic and the amp share this
 * one stream, so both directions run at 48 kHz.
 */

#include <linux/init.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>
#include <linux/slab.h>
#include <sound/pcm.h>
#include <sound/soc.h>

/* A733 I2S fixed clock. */
#define OWL_A733_RATE		48000U
#define OWL_A733_PLL_RATE	24576000U	/* 512 x 48 kHz */
#define OWL_A733_MCLK_FS	256		/* MCLK = 256 x fs = 12.288 MHz */
#define OWL_A733_SLOTS		2
#define OWL_A733_SLOT_WIDTH	32

/*
 * The one-bit data-delay quirk lives in bits [21:20] of each channel-select
 * register. data_late=1 (0x1 << 20) is the I2S value; set_fmt() resets it to 0.
 */
#define OWL_A733_I2S_TX0CHSEL	0x34
#define OWL_A733_I2S_TX1CHSEL	0x38
#define OWL_A733_I2S_TX2CHSEL	0x3c
#define OWL_A733_I2S_TX3CHSEL	0x40
#define OWL_A733_I2S_RXCHSEL	0x64
#define OWL_A733_I2S_DATA_DELAY_SHIFT	20
#define OWL_A733_I2S_DATA_DELAY_MASK \
	(0x3U << OWL_A733_I2S_DATA_DELAY_SHIFT)
#define OWL_A733_I2S_DATA_DELAY_I2S \
	(0x1U << OWL_A733_I2S_DATA_DELAY_SHIFT)

/*
 * Re-apply the one-bit I2S data delay. Called after every set_fmt(), which
 * resets these offset fields to zero.
 */
static int owl_a733_set_i2s_data_delay(struct snd_soc_dai *cpu_dai)
{
	static const unsigned int channel_select_regs[] = {
		OWL_A733_I2S_TX0CHSEL,
		OWL_A733_I2S_TX1CHSEL,
		OWL_A733_I2S_TX2CHSEL,
		OWL_A733_I2S_TX3CHSEL,
		OWL_A733_I2S_RXCHSEL,
	};
	struct regmap *regmap;
	int i, ret;

	regmap = dev_get_regmap(cpu_dai->dev, NULL);
	if (!regmap)
		return -ENODEV;

	for (i = 0; i < ARRAY_SIZE(channel_select_regs); i++) {
		ret = regmap_update_bits(regmap, channel_select_regs[i],
					 OWL_A733_I2S_DATA_DELAY_MASK,
					 OWL_A733_I2S_DATA_DELAY_I2S);
		if (ret)
			return ret;
	}

	return 0;
}

/*
 * Constrain ALSA to 48 kHz. The A733 BSP I2S path is only validated there and
 * the mic + amp share one BCLK, so a foreign rate cannot be serviced. This also
 * stops a misbehaving client from probing a clock setup in a tight retry loop.
 */
static int owl_dai_startup(struct snd_pcm_substream *substream)
{
	return snd_pcm_hw_constraint_single(substream->runtime,
					     SNDRV_PCM_HW_PARAM_RATE,
					     OWL_A733_RATE);
}

/*
 * Program the A733 I2S controller. Order matters (see the file header):
 * PLL first, then MCLK/sysclk, then the BCLK divider, then the format (which
 * clobbers the data-delay fields), then the data-delay fix, then the TDM slots.
 */
static int owl_dai_hw_params(struct snd_pcm_substream *substream,
			      struct snd_pcm_hw_params *params)
{
	struct snd_soc_pcm_runtime *rtd = substream->private_data;
	struct snd_soc_dai *cpu_dai = snd_soc_rtd_to_cpu(rtd, 0);
	unsigned int rate = params_rate(params);
	unsigned int bclk_ratio;
	int ret;

	if (rate != OWL_A733_RATE)
		return -EINVAL;

	ret = snd_soc_dai_set_pll(cpu_dai, substream->stream, 0,
				   OWL_A733_PLL_RATE, OWL_A733_PLL_RATE);
	if (ret)
		return dev_err_probe(rtd->dev, ret, "owl-i2s: set_pll failed\n");

	ret = snd_soc_dai_set_sysclk(cpu_dai, 0,
				      OWL_A733_RATE * OWL_A733_MCLK_FS,
				      SND_SOC_CLOCK_OUT);
	if (ret && ret != -ENOTSUPP)
		return ret;

	bclk_ratio = OWL_A733_PLL_RATE /
		     (rate * OWL_A733_SLOTS * OWL_A733_SLOT_WIDTH);
	ret = snd_soc_dai_set_bclk_ratio(cpu_dai, bclk_ratio);
	if (ret && ret != -ENOTSUPP)
		return ret;

	ret = snd_soc_dai_set_fmt(cpu_dai, rtd->dai_link->dai_fmt);
	if (ret && ret != -ENOTSUPP)
		return ret;

	ret = owl_a733_set_i2s_data_delay(cpu_dai);
	if (ret)
		return dev_err_probe(rtd->dev, ret,
				     "owl-i2s: data-delay fix failed\n");

	ret = snd_soc_dai_set_tdm_slot(cpu_dai, 0, 0,
					OWL_A733_SLOTS, OWL_A733_SLOT_WIDTH);
	if (ret && ret != -ENOTSUPP)
		return ret;

	return 0;
}

static const struct snd_soc_ops owl_dai_ops = {
	.startup = owl_dai_startup,
	.hw_params = owl_dai_hw_params,
};

static int owl_i2s_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct device_node *cpu_np;
	struct snd_soc_dai_link *dai_link;
	struct snd_soc_dai_link_component *cpus, *codecs, *platforms;
	struct snd_soc_card *card;
	int ret;

	cpu_np = of_parse_phandle(dev->of_node, "i2s-controller", 0);
	if (!cpu_np) {
		dev_err(dev, "owl-i2s: missing 'i2s-controller' phandle\n");
		return -EINVAL;
	}

	cpus = devm_kzalloc(dev, sizeof(*cpus), GFP_KERNEL);
	codecs = devm_kzalloc(dev, sizeof(*codecs), GFP_KERNEL);
	platforms = devm_kzalloc(dev, sizeof(*platforms), GFP_KERNEL);
	dai_link = devm_kzalloc(dev, sizeof(*dai_link), GFP_KERNEL);
	card = devm_kzalloc(dev, sizeof(*card), GFP_KERNEL);
	if (!cpus || !codecs || !platforms || !dai_link || !card) {
		ret = -ENOMEM;
		goto out_put_cpu;
	}

	cpus->of_node = cpu_np;
	/* No I2C codec on this card: the amp and mic are not ASoC codecs. */
	codecs->dai_name = "snd-soc-dummy";
	platforms->of_node = cpu_np;

	dai_link->name = "owl i2s";
	dai_link->stream_name = "Owl HiFi";
	dai_link->cpus = cpus;
	dai_link->num_cpus = 1;
	dai_link->codecs = codecs;
	dai_link->num_codecs = 1;
	dai_link->platforms = platforms;
	dai_link->num_platforms = 1;
	/*
	 * CPU is the bit/frame-clock master (NB_NF); the amp and mic are slaves
	 * (CFC_CFC). The continuous-clock flag keeps BCLK/LRCLK running when no
	 * stream is open, so the ICS43434 (which drops to standby without a
	 * running SCK) does not pay a ~10.7 ms wake on every playback open.
	 * Opt out with 'robotowl,no-continuous-clock' in the overlay if the
	 * A733 controller misbehaves with it.
	 */
	dai_link->dai_fmt = SND_SOC_DAIFMT_I2S | SND_SOC_DAIFMT_NB_NF |
			     SND_SOC_DAIFMT_CFC_CFC;
#ifdef SND_SOC_DAIFMT_CONT
	if (!of_property_read_bool(dev->of_node, "robotowl,no-continuous-clock"))
		dai_link->dai_fmt |= SND_SOC_DAIFMT_CONT;
#endif
	dai_link->ops = &owl_dai_ops;

	card->name = "owl";
	card->long_name = "Orange Pi Zero 3W Owl I2S (MAX98357A + ICS43434)";
	card->driver_name = "owl";
	card->dev = dev;
	card->owner = THIS_MODULE;
	card->dai_link = dai_link;
	card->num_links = 1;
	card->fully_routed = true;

	dev_set_drvdata(dev, card);

	ret = devm_snd_soc_register_card(dev, card);
	if (ret < 0) {
		if (ret != -EPROBE_DEFER)
			dev_err(dev, "owl-i2s: register card failed: %d\n", ret);
		goto out_put_cpu;
	}

	dev_info(dev, "owl-i2s: registered (48 kHz, 2 x 32-bit slots)\n");
	of_node_put(cpu_np);
	return 0;

out_put_cpu:
	of_node_put(cpu_np);
	return ret;
}

static const struct of_device_id owl_i2s_of_match[] = {
	{ .compatible = "robotowl,owl-i2s" },
	{ }
};
MODULE_DEVICE_TABLE(of, owl_i2s_of_match);

static struct platform_driver owl_i2s_driver = {
	.driver = {
		.name = "owl-i2s",
		.of_match_table = of_match_ptr(owl_i2s_of_match),
	},
	.probe = owl_i2s_probe,
};

module_platform_driver(owl_i2s_driver);

MODULE_DESCRIPTION("Robot Owl I2S sound card (A733, MAX98357A + ICS43434)");
MODULE_LICENSE("GPL");
